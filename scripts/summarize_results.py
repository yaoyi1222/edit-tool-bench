#!/usr/bin/env python3
"""Summarize an edit-tool-bench results CSV by edit arm."""
import argparse
import csv
import statistics
from collections import defaultdict


def as_bool(value):
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def as_float(value):
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt_int(value):
    return "" if value is None else f"{round(value):,}"


def fmt_float(value, digits=4):
    return "" if value is None else f"{value:.{digits}f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="results CSV produced by bench/orchestrate.py")
    args = parser.parse_args()

    by_arm = defaultdict(lambda: {"resolved": [], "tokens": [], "tools": [], "cost": [], "patch_bytes": []})
    with open(args.csv_path, newline="") as handle:
        for row in csv.DictReader(handle):
            arm = row["arm"]
            by_arm[arm]["resolved"].append(as_bool(row.get("resolved")))
            by_arm[arm]["tokens"].append(as_float(row.get("tokens_total")))
            by_arm[arm]["tools"].append(as_float(row.get("tool_calls_total")))
            by_arm[arm]["cost"].append(as_float(row.get("cost")))
            by_arm[arm]["patch_bytes"].append(as_float(row.get("patch_bytes")))

    print("| arm | n | resolved | pass rate | mean tokens | median tokens | mean cost | mean tool calls |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for arm in sorted(by_arm):
        data = by_arm[arm]
        resolved_values = [v for v in data["resolved"] if v is not None]
        n = len(data["resolved"])
        resolved = sum(1 for value in resolved_values if value)
        pass_rate = resolved / len(resolved_values) if resolved_values else None
        print(
            "| {arm} | {n} | {resolved} | {pass_rate} | {mean_tokens} | {median_tokens} | {mean_cost} | {mean_tools} |".format(
                arm=arm,
                n=n,
                resolved=resolved,
                pass_rate="" if pass_rate is None else f"{pass_rate:.1%}",
                mean_tokens=fmt_int(mean(data["tokens"])),
                median_tokens=fmt_int(median(data["tokens"])),
                mean_cost=fmt_float(mean(data["cost"])),
                mean_tools=fmt_float(mean(data["tools"]), digits=1),
            )
        )


if __name__ == "__main__":
    main()
