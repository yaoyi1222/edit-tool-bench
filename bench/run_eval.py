#!/usr/bin/env python3
"""Eval phase for ONE (instance, arm): feed the model patch to the official
SWE-bench Pro harness (local Docker) and read the resolved verdict.
"""
import argparse
import json
import os
import subprocess
import sys

HARNESS = os.environ.get("BENCH_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_REPO = os.path.join(HARNESS, "bench/SWE-bench_Pro-os")
RAW_SAMPLE = os.path.join(HARNESS, "bench/raw_sample.jsonl")
VENV_PY = os.environ.get("BENCH_VENV_PY") or os.path.join(HARNESS, "bench/.venv/bin/python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--patch", required=True, help="path to model_patch.diff")
    ap.add_argument("--out-dir", required=True, help="eval output dir for this (instance,arm)")
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    patch_text = open(args.patch).read()
    patches = [{"instance_id": args.instance_id, "patch": patch_text, "prefix": args.arm}]
    patches_path = os.path.join(out_dir, "patches.json")  # absolute: eval runs with cwd=EVAL_REPO
    json.dump(patches, open(patches_path, "w"))

    cmd = [
        VENV_PY, "swe_bench_pro_eval.py",
        f"--raw_sample_path={RAW_SAMPLE}",
        f"--patch_path={patches_path}",
        f"--output_dir={out_dir}",
        "--scripts_dir=run_scripts",
        "--use_local_docker",
        "--dockerhub_username=jefzda",
        "--num_workers=1",
    ]
    log = open(os.path.join(args.out_dir, "eval.log"), "w")
    try:
        subprocess.run(cmd, cwd=EVAL_REPO, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        log.write("\nEVAL TIMEOUT\n")
    log.close()

    resolved = None
    rp = os.path.join(out_dir, "eval_results.json")
    if os.path.exists(rp):
        res = json.load(open(rp))
        resolved = bool(res.get(args.instance_id, False))
    verdict = {"instance_id": args.instance_id, "arm": args.arm, "resolved": resolved}
    json.dump(verdict, open(os.path.join(args.out_dir, "verdict.json"), "w"), indent=2)
    print(f"[eval {args.arm}] {args.instance_id} resolved={resolved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
