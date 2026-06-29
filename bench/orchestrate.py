#!/usr/bin/env python3
"""Orchestrate the opencode edit-tool benchmark over SWE-bench Pro.

Outer loop is per-instance so we can pull one image, run all arms (edit + eval),
then reclaim the image to bound disk usage.

Usage:
  python orchestrate.py --arms C_anchor,legacy --instances <id1>[,<id2>] [--no-prune]
  python orchestrate.py --arms all --limit 5
  python orchestrate.py --arms all            # full batch (79 x arms)
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.environ.get("BENCH_ROOT") or os.path.dirname(HERE)
TASKS = json.load(open(os.path.join(HERE, "tasks.json")))
ARMS = json.load(open(os.path.join(HERE, "arms.json")))
TASKS_BY_ID = {t["instance_id"]: t for t in TASKS}
RUNS = os.environ.get("BENCH_RUNS") or os.path.join(HARNESS, "runs")
PLATFORM = "linux/amd64"
# edit phase script: in-container (faithful) by default; host (blind) optional
EDIT_SCRIPT = {"container": "run_edit_container.py", "host": "run_edit.py"}
VENV_PY = os.environ.get("BENCH_VENV_PY") or os.path.join(HERE, ".venv/bin/python")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def docker_pull(image, attempts=6):
    # Docker Hub / CloudFront intermittently drops large pulls (EOF, TLS timeout);
    # completed layers are cached, so retry resumes.
    last = ""
    for i in range(1, attempts + 1):
        print(f"  pull {image} (attempt {i})")
        sh(["docker", "pull", "--platform", PLATFORM, image], timeout=3600)
        if sh(["docker", "image", "inspect", image]).returncode == 0:
            return True, "ok"
        last = "pull incomplete"
    return False, last


def docker_rmi(image):
    sh(["docker", "rmi", "-f", image])
    sh(["docker", "image", "prune", "-f"])


def done_marker(arm, iid):
    return os.path.join(RUNS, "eval", arm, iid, "verdict.json")


def run_instance(iid, arms, edit_timeout, eval_timeout, prune, edit_mode, jobs, eval_jobs):
    task = TASKS_BY_ID[iid]
    image = f"jefzda/sweap-images:{task['dockerhub_tag']}"
    rows = []

    pending = [a for a in arms if not os.path.exists(done_marker(a, iid))]
    if not pending:
        print(f"== {iid}: all arms done, skipping")
        for a in arms:
            rows.append(read_row(a, iid))
        return rows

    ok, msg = docker_pull(image)
    if not ok:
        print(f"== {iid}: PULL FAILED: {msg}")
        for a in arms:
            rows.append({"instance_id": iid, "arm": a, "repo": task["repo"], "lang": task["repo_language"],
                         "error": "pull_failed", "resolved": None})
        return rows

    try:
        task_json = os.path.join(RUNS, "_tasks", f"{iid}.json")
        os.makedirs(os.path.dirname(task_json), exist_ok=True)
        json.dump(task, open(task_json, "w"))

        def do_edit(arm):
            edit_out = os.path.join(RUNS, arm, iid)
            if os.path.exists(os.path.join(edit_out, "model_patch.diff")):
                return
            print(f"  edit [{arm}] {iid} ({edit_mode})")
            cmd = [sys.executable, os.path.join(HERE, EDIT_SCRIPT[edit_mode]),
                   "--task-json", task_json, "--arm", arm, "--edit-tool", ARMS[arm],
                   "--image", image, "--out-dir", edit_out, "--timeout", str(edit_timeout)]
            if edit_mode == "host":
                cmd += ["--workspace", os.path.join(RUNS, "workspaces", arm, iid)]
            sh(cmd, timeout=edit_timeout + 300)

        def do_eval(arm):
            if os.path.exists(done_marker(arm, iid)):
                return
            patch = os.path.join(RUNS, arm, iid, "model_patch.diff")
            if not os.path.exists(patch):
                return
            print(f"  eval [{arm}] {iid}")
            sh([VENV_PY, os.path.join(HERE, "run_eval.py"),
                "--instance-id", iid, "--arm", arm, "--patch", patch,
                "--out-dir", os.path.join(RUNS, "eval", arm, iid), "--timeout", str(eval_timeout)],
               timeout=eval_timeout + 300)

        todo = [a for a in arms if not os.path.exists(done_marker(a, iid))]
        # edits are LLM-latency bound -> wider concurrency; evals are CPU bound -> narrower
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            list(ex.map(do_edit, todo))
        with ThreadPoolExecutor(max_workers=max(1, eval_jobs)) as ex:
            list(ex.map(do_eval, todo))

        if prune and edit_mode == "host":
            for arm in arms:
                shutil.rmtree(os.path.join(RUNS, "workspaces", arm, iid), ignore_errors=True)
        for arm in arms:
            rows.append(read_row(arm, iid))
    finally:
        if prune:
            docker_rmi(image)
    return rows


def read_row(arm, iid):
    task = TASKS_BY_ID[iid]
    row = {"instance_id": iid, "arm": arm, "repo": task["repo"], "lang": task["repo_language"], "model": None,
           "resolved": None, "patch_bytes": 0, "tokens_total": None, "tool_calls": None,
           "cost": None, "steps": None, "duration_ms": None}
    st = os.path.join(RUNS, arm, iid, "status.json")
    if os.path.exists(st):
        s = json.load(open(st))
        row["patch_bytes"] = s.get("patch_bytes", 0)
        row["model"] = s.get("model")
    me = os.path.join(RUNS, arm, iid, "metrics.json")
    if os.path.exists(me):
        m = json.load(open(me))
        row["tokens_total"] = (m.get("tokens") or {}).get("total")
        row["tool_calls"] = json.dumps(m.get("tool_calls_by_name"))
        row["cost"] = m.get("cost")
        row["steps"] = m.get("steps")
        row["duration_ms"] = m.get("duration_ms")
        row["tool_calls_total"] = m.get("tool_calls_total")
    vd = os.path.join(RUNS, "eval", arm, iid, "verdict.json")
    if os.path.exists(vd):
        row["resolved"] = json.load(open(vd)).get("resolved")
    return row


def aggregate(all_rows):
    os.makedirs(RUNS, exist_ok=True)
    out = os.path.join(RUNS, "results.csv")
    cols = ["instance_id", "arm", "repo", "lang", "model", "resolved", "patch_bytes", "tokens_total",
            "tool_calls_total", "tool_calls", "cost", "steps", "duration_ms"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"\nwrote {out}")
    # per-arm summary
    print("\n=== per-arm summary ===")
    by_arm = {}
    for r in all_rows:
        a = r["arm"]
        d = by_arm.setdefault(a, {"n": 0, "resolved": 0, "tokens": 0, "tools": 0, "cost": 0.0})
        d["n"] += 1
        if r.get("resolved"):
            d["resolved"] += 1
        if r.get("tokens_total"):
            d["tokens"] += r["tokens_total"]
        if r.get("tool_calls_total"):
            d["tools"] += r["tool_calls_total"]
        if r.get("cost"):
            d["cost"] += r["cost"]
    for a, d in by_arm.items():
        n = d["n"] or 1
        print(f"  {a:14s} resolved {d['resolved']}/{d['n']} "
              f"({d['resolved']/n:.1%})  mean_tokens={d['tokens']//n}  "
              f"mean_tools={d['tools']/n:.1f}  total_cost=${d['cost']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="all")
    ap.add_argument("--instances", default="all")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--edit-timeout", type=int, default=1800)
    ap.add_argument("--eval-timeout", type=int, default=2400)
    ap.add_argument("--edit-mode", choices=["container", "host"], default="container")
    ap.add_argument("--jobs", type=int, default=4, help="parallel edit workers per instance")
    ap.add_argument("--eval-jobs", type=int, default=3, help="parallel eval workers per instance")
    ap.add_argument("--no-prune", action="store_true", help="keep images/workspaces (pilot)")
    args = ap.parse_args()

    arms = list(ARMS.keys()) if args.arms == "all" else args.arms.split(",")
    if args.instances == "all":
        iids = [t["instance_id"] for t in TASKS]
    else:
        iids = args.instances.split(",")
    if args.limit:
        iids = iids[: args.limit]

    print(f"arms={arms}\ninstances={len(iids)}  prune={not args.no_prune}  edit_mode={args.edit_mode}")
    all_rows = []
    for i, iid in enumerate(iids, 1):
        print(f"\n[{i}/{len(iids)}] {iid}")
        all_rows.extend(run_instance(iid, arms, args.edit_timeout, args.eval_timeout,
                                     prune=not args.no_prune, edit_mode=args.edit_mode,
                                     jobs=args.jobs, eval_jobs=args.eval_jobs))
        aggregate(all_rows)  # write incrementally so progress is durable


if __name__ == "__main__":
    main()
