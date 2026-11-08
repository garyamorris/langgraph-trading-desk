#!/usr/bin/env python3
"""Write manifest_final.json for build_video2.py from: narration/durations.json, narration/captions.json (word-timed subtitle cues),
and the recorded footage + its markers in raw/.  Re-run after re-recording or re-voicing; nothing here is hand-timed except the
source ranges below, which are derived from the run's own node-start / node-end markers."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
J = lambda p: json.load(open(os.path.join(HERE, p)))
dur, caps = J("narration/durations.json"), J("narration/captions.json")
mk = J("raw/run.mp4.markers.json")
PAD_BEFORE, PAD_AFTER = 0.4, 0.6


def first(name, id_=None, run=None, last=False):
    hits = [m for m in mk if m["name"] == name and (id_ is None or (m.get("data") or {}).get("id") == id_) and (run is None or (m.get("data") or {}).get("run") == run)]
    if not hits:
        raise SystemExit(f"marker not found: {name} {id_} {run}")
    return (hits[-1] if last else hits[0])["t"]


t_cio = first("node-start", "cio_regime_router")
t_agg = first("node-start", "intelligence_aggregator")
t_debate = first("node-start", "bear_thesis_critic")
t_arb = first("node-start", "adversarial_debate_arbiter")
t_desks = first("node-start", "g:strategy_desk", run=1)
t_aud = first("node-start", "factor_exposure_auditor", run=1)
t_cro1 = first("node-start", "cro_risk_synthesis", run=1)
t_rev = first("node-start", "g:strategy_desk", run=2)
t_cro2 = first("node-start", "cro_risk_synthesis", run=2)
t_head = first("node-start", "head_of_trading")
t_exec = first("node-start", "execution_microstructure_router")
t_done = first("run-done")
t_over = first("camera", None) if False else [m for m in mk if m["name"] == "camera" and (m.get("data") or {}).get("stage") == "overview"][-1]["t"]
t_end = first("run-end")
print({k: round(v, 1) for k, v in dict(cio=t_cio, agg=t_agg, debate=t_debate, arb=t_arb, desks=t_desks, aud=t_aud, cro1=t_cro1, rev=t_rev, cro2=t_cro2, head=t_head, exec=t_exec, done=t_done, over=t_over, end=t_end).items()})

V = lambda path, a, b, **k: {"type": "video", "path": path, "in": round(a, 2), "out": round(b, 2), "speed": "fit", **k}
FREEZE = lambda path, t, **k: {"type": "video", "path": path, "in": round(t, 2), "freeze": True, "move": "none", **k}
RUN = "raw/run.mp4"

SEG = {
    "A": [V("raw/tour_a.mp4", 0, 19.0)],
    "B": [V("raw/state_b.mp4", 0, 21.1)],
    # the analysts' model calls take most of this stretch: play the start and the aggregator merge at about real speed, the wait fast
    "C": [V(RUN, t_cio - 0.8, t_cio + 11.5, weight=0.40), V(RUN, t_cio + 11.5, t_agg - 3.0, weight=0.25), V(RUN, t_agg - 3.0, t_debate + 1.5, weight=0.35)],
    "D": [V(RUN, t_debate - 0.5, t_desks + 1.0)],
    "E": [V(RUN, t_desks, t_aud + 0.5)],
    "F": [V(RUN, t_aud - 1.5, t_rev + 2.0)],
    "G": [V(RUN, t_rev - 0.5, t_head + 0.2)],
    "H": [V("raw/fail_h.mp4", 0, 16.45)],
    "I": [V(RUN, t_head - 0.5, t_exec + 2.0, weight=0.5), V(RUN, t_done - 9.0, t_over + 1.4, weight=0.5)],
    "K": [V(RUN, t_over - 0.6, t_end - 0.4, weight=1.0), FREEZE(RUN, t_end - 0.4, weight=1.2)],
}
TAGS = {}
scenes = []
for sid in [s["id"] for s in J("narration/script.json")["scenes"]]:
    cues = [{"at": round(PAD_BEFORE + c["start"], 3), "dur": round(c["end"] - c["start"], 3), "text": c["text"], "style": "caption", "anchor": "bc"} for c in caps[sid]["cues"]]
    scenes.append({"id": sid, "segments": SEG[sid], "callouts": cues, "pad_before": PAD_BEFORE, "pad_after": PAD_AFTER})

manifest = {
    "width": 1920, "height": 1080, "fps": 30, "narration_dir": "narration/audio",
    "preset": "Match Source – Adaptive Low Bitrate", "max_mb": 60, "audio_kbps": 128,
    "small": {"width": 1280, "height": 720, "max_mb": 28, "audio_kbps": 96},
    "xfade": 0.35, "narration_db": -3.0,
    "title": {"text": "trading_desk", "sub": "how a LangGraph hedge-fund desk runs, from a real recorded run", "seconds": 3.5,
              "bg": FREEZE(RUN, t_over + 2.0), "dim": -90},
    "outro": {"text": "Run it yourself", "sub": "uv run langgraph dev  ·  paper data, not trading advice", "seconds": 3.5,
              "bg": FREEZE(RUN, t_end - 0.4), "dim": -90},
    "scenes": scenes,
}
json.dump(manifest, open(os.path.join(HERE, "manifest_final.json"), "w"), indent=1)
print("wrote manifest_final.json:", len(scenes), "scenes,", sum(len(s["callouts"]) for s in scenes), "subtitle callouts")
