#!/usr/bin/env python3
"""In-container edit phase for ONE (instance, arm).

Runs the standalone opencode binary INSIDE the SWE-bench Pro image so the agent
has the real toolchain (can compile / run tests / iterate). Produces the same
artifacts as run_edit.py: events.jsonl, run.log, model_patch.diff, transcript.json,
metrics.json, status.json, prompt.txt.

The agent edits /app at base_commit; we then `git diff` to get the patch. The
official grader (run_eval.py) is unchanged.
"""
import argparse
import json
import os
import subprocess
import sys

HARNESS = os.environ.get("BENCH_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# baseline (no-AVX2) build runs on any x64 (incl. emulation). On a native amd64
# host you may point OPENCODE_BIN at a non-baseline build for speed.
OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or os.path.join(HARNESS, "bench/opencode-linux-x64-baseline")
GLIBC_RUNTIME = [
    "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    "/lib/x86_64-linux-gnu/libc.so.6",
    "/lib/x86_64-linux-gnu/libpthread.so.0",
    "/lib/x86_64-linux-gnu/libdl.so.2",
    "/lib/x86_64-linux-gnu/libm.so.6",
]
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics as metrics_mod  # noqa: E402
from run_edit import API_KEY_ENV, MODEL, OPENCODE_CONFIG, provider_key, build_prompt  # reuse helpers  # noqa: E402


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def dexec(container, args, env=None, **kw):
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [container] + args
    return subprocess.run(cmd, **kw)


def inject_glibc_runtime(container):
    missing = [path for path in GLIBC_RUNTIME if not os.path.exists(path)]
    if missing:
        return False, "missing host glibc runtime files: " + ", ".join(missing)

    mk = dexec(container, ["mkdir", "-p", "/glibc", "/lib64"], capture_output=True, text=True)
    if mk.returncode != 0:
        return False, "mkdir glibc runtime dirs failed: " + ((mk.stderr or mk.stdout or "")[:200])

    for src in GLIBC_RUNTIME:
        cp = sh(["docker", "cp", src, f"{container}:/glibc/{os.path.basename(src)}"])
        if cp.returncode != 0:
            return False, f"docker cp {src} failed: " + ((cp.stderr or cp.stdout or "")[:200])

    ln = dexec(
        container,
        ["ln", "-sf", "/glibc/ld-linux-x86-64.so.2", "/lib64/ld-linux-x86-64.so.2"],
        capture_output=True,
        text=True,
    )
    if ln.returncode != 0:
        return False, "link glibc loader failed: " + ((ln.stderr or ln.stdout or "")[:200])

    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-json", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--edit-tool", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    task = json.load(open(args.task_json))
    iid = task["instance_id"]
    base = task["base_commit"]
    os.makedirs(args.out_dir, exist_ok=True)
    status = {"instance_id": iid, "arm": args.arm, "ok": False, "mode": "in_container", "model": MODEL}
    container = f"edit_{args.arm}_{abs(hash(iid)) % 10**8}"

    # Fail fast & loud: a missing binary used to silently produce empty patches
    # (docker cp would fail, opencode exec returned 127, 0 tokens, blank patch).
    if not os.path.exists(OPENCODE_BIN):
        status["reason"] = f"OPENCODE_BIN not found: {OPENCODE_BIN}"
        json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
        print("BIN MISSING:", OPENCODE_BIN); return 1

    sh(["docker", "rm", "-f", container])
    # Keep container alive; override the image's default bash entrypoint.
    r = sh(["docker", "run", "-d", "--name", container, "--platform", "linux/amd64",
            "--entrypoint", "/bin/bash", args.image, "-c", "sleep infinity"])
    if r.returncode != 0:
        status["reason"] = "docker run failed: " + r.stderr[:200]
        json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
        print("RUN FAILED:", r.stderr[:200]); return 1

    try:
        # inject opencode binary + config
        cp = sh(["docker", "cp", OPENCODE_BIN, f"{container}:/usr/local/bin/opencode"])
        if cp.returncode != 0:
            status["reason"] = "docker cp opencode failed: " + (cp.stderr or "")[:200]
            json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
            sh(["docker", "rm", "-f", container])
            print("CP FAILED:", (cp.stderr or "")[:200]); return 1
        dexec(container, ["chmod", "+x", "/usr/local/bin/opencode"])
        sh(["docker", "cp", OPENCODE_CONFIG, f"{container}:/cfg.json"])
        # Sanity: binary must actually execute in THIS image (catches arch/libc
        # mismatch up front instead of as a blank 127 patch).
        opencode_env = {}
        ver = dexec(container, ["opencode", "--version"], env=opencode_env, capture_output=True, text=True)
        if ver.returncode != 0:
            first_error = (ver.stderr or ver.stdout or "")[:200]
            injected, inject_error = inject_glibc_runtime(container)
            if injected:
                opencode_env = {"LD_LIBRARY_PATH": "/glibc"}
                ver = dexec(container, ["opencode", "--version"], env=opencode_env, capture_output=True, text=True)
                if ver.returncode == 0:
                    status["glibc_runtime"] = "injected"
            if ver.returncode != 0:
                detail = (ver.stderr or ver.stdout or inject_error or "")[:200]
                status["reason"] = (
                    f"opencode --version rc={ver.returncode}: {detail}; "
                    f"initial_error={first_error}; glibc_inject_error={inject_error}"
                )
                json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
                sh(["docker", "rm", "-f", container])
                print("OPENCODE EXEC FAILED:", status["reason"]); return 1

        # reset repo to base commit (no test-file checkout; that's eval-only)
        dexec(container, ["bash", "-lc", f"cd /app && git reset --hard {base} && git checkout {base} && git clean -fd"],
              capture_output=True, text=True)

        prompt = build_prompt(task)
        open(os.path.join(args.out_dir, "prompt.txt"), "w").write(prompt)

        env = {API_KEY_ENV: provider_key(), "OPENCODE_EDIT_TOOL": args.edit_tool,
               "OPENCODE_CONFIG": "/cfg.json", "HOME": "/root",
               # China mirrors so opencode can auto-install LSP servers in-container
               # (gopls via `go install`; pyright/typescript-language-server via npm).
               "GOPROXY": "https://goproxy.cn,direct", "GOSUMDB": "off",
               "GOFLAGS": "-mod=mod", "npm_config_registry": "https://registry.npmmirror.com"}
        env.update(opencode_env)
        events_path = os.path.join(args.out_dir, "events.jsonl")
        log_path = os.path.join(args.out_dir, "run.log")
        with open(events_path, "w") as out, open(log_path, "w") as err:
            try:
                p = dexec(container,
                          ["opencode", "run", prompt, "--model", MODEL, "--format", "json",
                           "--dangerously-skip-permissions", "--dir", "/app"],
                          env=env, stdout=out, stderr=err, timeout=args.timeout)
                status["opencode_rc"] = p.returncode
            except subprocess.TimeoutExpired:
                status["opencode_error"] = "timeout"

        # extract patch
        r = dexec(container, ["bash", "-lc", f"cd /app && git add -A && git -c core.fileMode=false diff --cached {base}"],
                  capture_output=True, text=True)
        patch = r.stdout
        open(os.path.join(args.out_dir, "model_patch.diff"), "w").write(patch)
        status["patch_bytes"] = len(patch)

        m = metrics_mod.parse_events(events_path)
        json.dump(m, open(os.path.join(args.out_dir, "metrics.json"), "w"), indent=2)

        # transcript (best effort, in-container export)
        sid = m.get("session_id")
        if sid:
            export_env = {"HOME": "/root"}
            export_env.update(opencode_env)
            t = dexec(container, ["opencode", "export", sid], env=export_env, capture_output=True, text=True, timeout=120)
            if t.returncode == 0 and t.stdout.strip():
                open(os.path.join(args.out_dir, "transcript.json"), "w").write(t.stdout)

        status["ok"] = patch.strip() != ""
        status["tool_calls"] = m.get("tool_calls_by_name")
        status["tokens_total"] = m.get("tokens", {}).get("total")
    finally:
        sh(["docker", "rm", "-f", container])

    json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
    print(f"[{args.arm}] {iid} patch_bytes={status.get('patch_bytes')} "
          f"tools={status.get('tool_calls')} tokens={status.get('tokens_total')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
