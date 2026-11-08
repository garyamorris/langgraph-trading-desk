#!/usr/bin/env python3
"""Run the real trading_desk graph on the local LangGraph server and save every stream event with a timestamp.

    uv run python docs/demo/video-source/capture_run.py OUT.jsonl [--api http://127.0.0.1:2024] [--max-revisions 2]

The viz page (viz/live.js) consumes the very same stream, so this is a way to rehearse a run, measure how long each
phase takes, and mine real numbers (tickers, notionals, breaches) for the narration without opening a browser.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

PROMPT = (
    "Run our full 24-node institutional trading workflow across NVDA, AMD, MSFT, SPY, QQQ, and TLT: "
    "dispatch the 6-agent Intelligence Syndicate and single-ticker deep research workers, run the Bull vs Bear debate, "
    "execute all 4 Strategy Desks (Momentum pitching a $15M NVDA lead trade), run the Triad Risk Committee with selective "
    "desk re-dispatch until approved, then generate the Head of Trading blotter and the EMS child-order plan."
)
TICKERS = ["NVDA", "AMD", "MSFT", "SPY", "QQQ", "TLT"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--api", default="http://127.0.0.1:2024")
    ap.add_argument("--max-revisions", type=int, default=2)
    a = ap.parse_args()

    with httpx.Client(timeout=None) as c:
        aid = c.post(f"{a.api}/assistants/search", json={"graph_id": "trading_desk", "limit": 1}).json()[0]["assistant_id"]
        tid = c.post(f"{a.api}/threads", json={"metadata": {"source": "capture_run"}}).json()["thread_id"]
        body = {
            "assistant_id": aid,
            "input": {
                "messages": [{"type": "human", "content": PROMPT}],
                "focus_tickers": TICKERS,
                "revision_count": 0,
                "max_revisions": a.max_revisions,
            },
            "stream_mode": ["values", "updates", "tasks", "checkpoints", "custom"],
            "stream_subgraphs": True,
        }
        t0 = time.time()
        print(f"thread {tid}", flush=True)
        n = 0
        with open(a.out, "w") as f, c.stream("POST", f"{a.api}/threads/{tid}/runs/stream", json=body) as r:
            r.raise_for_status()
            ev, data = None, []
            for line in r.iter_lines():
                if line == "":
                    if ev is not None or data:
                        n += 1
                        rec = {"t": round(time.time() - t0, 3), "event": ev, "data": "\n".join(data)}
                        f.write(json.dumps(rec) + "\n")
                        if ev and ev.startswith("tasks"):
                            try:
                                d = json.loads(rec["data"])
                                kind = "end" if "result" in d else "start"
                                print(f"[{rec['t']:7.1f}s] {kind:5s} {ev.split('|', 1)[-1] if '|' in ev else ''} {d.get('name')}", flush=True)
                            except Exception:  # noqa: BLE001
                                pass
                    ev, data = None, []
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
        print(f"done: {n} events in {time.time() - t0:.1f}s thread={tid}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
