#!/usr/bin/env python3
"""Parse an opencode `--format json` event stream (events.jsonl) into run metrics.

Event shape (one JSON object per line):
  {"type": <part-type>, "timestamp": <ms>, "sessionID": <id>, "part": {...}}

We care about:
  - step_finish parts -> token usage + cost (summed across steps)
  - tool parts        -> tool-call counts by name (deduped by part id)
  - text parts        -> assistant text (kept for replay)
"""
import json
import sys


def parse_events(path):
    steps = 0
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "total": 0}
    cost = 0.0
    # dedupe tool calls by part id; remember final state + name
    tool_calls = {}  # id -> {tool, status}
    text_chunks = 0
    first_ts = None
    last_ts = None
    session_id = None
    errors = []

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = e.get("timestamp")
            if ts is not None:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            session_id = session_id or e.get("sessionID")
            p = e.get("part") or {}
            # Top-level event types use underscores (step_finish, tool_use, text);
            # the nested part.type uses hyphens (step-finish, tool, text).
            t = e.get("type")
            if t == "step_finish":
                tk = p.get("tokens") or {}
                tokens["input"] += tk.get("input", 0) or 0
                tokens["output"] += tk.get("output", 0) or 0
                tokens["reasoning"] += tk.get("reasoning", 0) or 0
                cache = tk.get("cache") or {}
                tokens["cache_read"] += cache.get("read", 0) or 0
                tokens["cache_write"] += cache.get("write", 0) or 0
                tokens["total"] += tk.get("total", 0) or 0
                cost += p.get("cost", 0) or 0
                steps += 1
            elif t == "tool_use":
                pid = p.get("id") or p.get("callID") or id(p)
                state = p.get("state") or {}
                tool_calls[pid] = {"tool": p.get("tool"), "status": state.get("status")}
            elif t == "text":
                text_chunks += 1
            elif t == "error":
                errors.append(str(p)[:200])

    by_tool = {}
    for v in tool_calls.values():
        by_tool[v["tool"]] = by_tool.get(v["tool"], 0) + 1

    return {
        "session_id": session_id,
        "steps": steps,
        "tokens": tokens,
        "cost": round(cost, 6),
        "tool_calls_total": len(tool_calls),
        "tool_calls_by_name": by_tool,
        "text_chunks": text_chunks,
        "duration_ms": (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else None,
        "errors": errors,
    }


if __name__ == "__main__":
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    m = parse_events(path)
    s = json.dumps(m, indent=2)
    if out:
        open(out, "w").write(s)
    print(s)
