#!/usr/bin/env python3
"""Download SWE-bench Pro test split, filter to the selected instance_ids, save full records."""
import json, os, sys, urllib.request
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARQUET_URL = "https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro/resolve/main/data/test-00000-of-00001.parquet"
PARQUET_LOCAL = os.path.join(HERE, "swe_bench_pro_test.parquet")
SELECTED = os.path.join(HERE, "selected_ids.json")
OUT = os.path.join(HERE, "tasks.json")


def main():
    if not os.path.exists(PARQUET_LOCAL):
        print(f"downloading {PARQUET_URL}")
        urllib.request.urlretrieve(PARQUET_URL, PARQUET_LOCAL)
    df = pd.read_parquet(PARQUET_LOCAL)
    print("dataset rows:", len(df), "columns:", list(df.columns))

    ids = set(json.load(open(SELECTED))["ids"])
    sub = df[df["instance_id"].isin(ids)].copy()
    print(f"matched {len(sub)} / {len(ids)} selected ids")

    missing = ids - set(sub["instance_id"])
    if missing:
        print(f"WARNING: {len(missing)} selected ids not found in dataset:")
        for m in sorted(missing):
            print("  ", m)

    records = []
    for _, row in sub.iterrows():
        rec = {}
        for k, v in row.items():
            # normalize numpy/array types to JSON-friendly
            try:
                json.dumps(v)
                rec[k] = v
            except (TypeError, ValueError):
                rec[k] = v.tolist() if hasattr(v, "tolist") else str(v)
        records.append(rec)

    json.dump(records, open(OUT, "w"), indent=2, default=str)
    print(f"wrote {len(records)} records to {OUT}")
    # quick profile
    langs = {}
    for r in records:
        langs[r.get("repo_language")] = langs.get(r.get("repo_language"), 0) + 1
    print("by language:", langs)


if __name__ == "__main__":
    main()
