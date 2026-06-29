#!/usr/bin/env python3
"""Edit phase for ONE (instance, arm): extract repo from image, run opencode with the
arm's edit-tool set, capture the raw event stream, and produce a git patch.

Outputs into <out_dir>/:
  events.jsonl     raw opencode --format json stream (model I/O log)
  run.log          opencode stderr
  model_patch.diff git diff of the agent's changes vs base_commit
  transcript.json  opencode export of the session (best effort)
  metrics.json     parsed token/tool metrics
  status.json      {ok, reason, patch_bytes, ...}
"""
import argparse
import json
import os
import subprocess
import sys

HARNESS = os.environ.get("BENCH_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENCODE_DIR = os.path.join(HARNESS, "opencode/packages/opencode")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics as metrics_mod  # noqa: E402


def dotenv_values():
    path = os.path.join(HARNESS, ".env")
    values = {}
    if not os.path.exists(path):
        return values
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


DOTENV = dotenv_values()


def env_value(name, default=None):
    return os.environ.get(name) or DOTENV.get(name) or default


def harness_path(path):
    return path if os.path.isabs(path) else os.path.join(HARNESS, path)


OPENCODE_CONFIG = harness_path(env_value("OPENCODE_CONFIG", "bench/opencode-deepseek.json"))
MODEL = env_value("OPENCODE_MODEL", "deepseek/deepseek-v4-flash")
API_KEY_ENV = env_value("OPENCODE_API_KEY_ENV", "DEEPSEEK_API_KEY")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def provider_key():
    key = env_value(API_KEY_ENV) or env_value("API_KEY")
    if key:
        return key
    raise RuntimeError(f"{API_KEY_ENV} or API_KEY not found in environment or .env")


def deepseek_key():
    return provider_key()


def extract_repo(image, base_commit, ws):
    """docker cp /app out of the image into ws, reset to base_commit."""
    os.makedirs(ws, exist_ok=True)
    name = "swebench_extract_" + str(os.getpid()) + "_" + str(abs(hash(ws)) % 10**8)
    sh(["docker", "rm", "-f", name])
    r = sh(["docker", "create", "--name", name, image])
    if r.returncode != 0:
        return False, f"docker create failed: {r.stderr[:200]}"
    try:
        # copy contents of /app into ws
        r = sh(["docker", "cp", f"{name}:/app/.", ws])
        if r.returncode != 0:
            return False, f"docker cp failed: {r.stderr[:200]}"
    finally:
        sh(["docker", "rm", "-f", name])
    if os.path.isdir(os.path.join(ws, ".git")):
        sh(["git", "-C", ws, "reset", "--hard", base_commit])
        sh(["git", "-C", ws, "checkout", base_commit])
        sh(["git", "-C", ws, "clean", "-fd"])
    else:
        return False, "no .git in extracted /app"
    return True, "ok"


def build_prompt(task):
    parts = [
        f"You are resolving an issue in the `{task['repo']}` repository. "
        "Implement the necessary code changes in the current working directory so the described "
        "behavior is satisfied. Do NOT modify or add test files; only change implementation code. "
        "You do not have internet access.",
        "\n# Problem statement\n" + (task.get("problem_statement") or ""),
    ]
    if task.get("requirements"):
        parts.append("\n# Requirements\n" + task["requirements"])
    if task.get("interface"):
        parts.append("\n# Interface\n" + task["interface"])
    return "\n".join(parts)


def run_opencode(ws, edit_tool, prompt, out_dir, timeout):
    env = dict(os.environ)
    env[API_KEY_ENV] = provider_key()
    env["OPENCODE_CONFIG"] = OPENCODE_CONFIG
    env["OPENCODE_EDIT_TOOL"] = edit_tool  # "legacy" or comma-list
    cmd = [
        "bun", "run", "--conditions=browser", "./src/index.ts", "run", prompt,
        "--model", MODEL, "--format", "json", "--dangerously-skip-permissions", "--dir", ws,
    ]
    events_path = os.path.join(out_dir, "events.jsonl")
    log_path = os.path.join(out_dir, "run.log")
    with open(events_path, "w") as out, open(log_path, "w") as err:
        try:
            p = subprocess.run(cmd, cwd=OPENCODE_DIR, env=env, stdout=out, stderr=err, timeout=timeout)
            return p.returncode, None
        except subprocess.TimeoutExpired:
            return None, "timeout"


def git_patch(ws, base_commit, out_dir):
    sh(["git", "-C", ws, "add", "-A"])
    r = subprocess.run(
        ["git", "-C", ws, "-c", "core.fileMode=false", "diff", "--cached", base_commit],
        capture_output=True, text=True,
    )
    patch = r.stdout
    open(os.path.join(out_dir, "model_patch.diff"), "w").write(patch)
    return patch


def export_transcript(session_id, out_dir):
    if not session_id:
        return
    try:
        r = subprocess.run(
            ["bun", "run", "--conditions=browser", "./src/index.ts", "export", session_id],
            cwd=OPENCODE_DIR, capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            open(os.path.join(out_dir, "transcript.json"), "w").write(r.stdout)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-json", required=True, help="path to a single-task JSON file")
    ap.add_argument("--arm", required=True)
    ap.add_argument("--edit-tool", required=True, help="OPENCODE_EDIT_TOOL value")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    task = json.load(open(args.task_json))
    os.makedirs(args.out_dir, exist_ok=True)
    status = {"instance_id": task["instance_id"], "arm": args.arm, "ok": False, "model": MODEL}

    ok, reason = extract_repo(args.image, task["base_commit"], args.workspace)
    if not ok:
        status["reason"] = "extract: " + reason
        json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
        print("EXTRACT FAILED:", reason)
        return 1

    prompt = build_prompt(task)
    open(os.path.join(args.out_dir, "prompt.txt"), "w").write(prompt)
    rc, err = run_opencode(args.workspace, args.edit_tool, prompt, args.out_dir, args.timeout)
    status["opencode_rc"] = rc
    status["opencode_error"] = err

    patch = git_patch(args.workspace, task["base_commit"], args.out_dir)
    status["patch_bytes"] = len(patch)

    m = metrics_mod.parse_events(os.path.join(args.out_dir, "events.jsonl"))
    json.dump(m, open(os.path.join(args.out_dir, "metrics.json"), "w"), indent=2)
    export_transcript(m.get("session_id"), args.out_dir)

    status["ok"] = patch.strip() != ""
    status["tool_calls"] = m.get("tool_calls_by_name")
    status["tokens_total"] = m.get("tokens", {}).get("total")
    json.dump(status, open(os.path.join(args.out_dir, "status.json"), "w"), indent=2)
    print(f"[{args.arm}] {task['instance_id']} patch_bytes={status['patch_bytes']} "
          f"tools={m.get('tool_calls_by_name')} tokens={status['tokens_total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
