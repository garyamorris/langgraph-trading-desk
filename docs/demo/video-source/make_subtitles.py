#!/usr/bin/env python3
"""Write the .srt, .vtt and a readable transcript next to the finished video, from the build plan (scene voice-over start times) and
narration/captions.json (word-timed cues).   python3 make_subtitles.py out_final.plan.json ../trading-desk-walkthrough"""
import json, sys

plan, stem = sys.argv[1], sys.argv[2]
P = json.load(open(plan))
caps = json.load(open("narration/captions.json"))
script = {s["id"]: s["text"] for s in json.load(open("narration/script.json"))["scenes"]}
rows, transcript = [], ["# trading_desk walkthrough: transcript\n",
    "Narration as spoken (OpenAI `gpt-4o-mini-tts`, voice marin). Captions use this text, timed to the real audio by a `whisper-1` word-level alignment.\n"]
mmss = lambda t: f"{int(t // 60)}:{t % 60:04.1f}"
for sc in P["scenes"]:
    if sc["id"] not in caps:
        continue
    t0 = sc["vo_start"]
    transcript.append(f"## [{mmss(t0)}] {sc['id']}\n\n{script[sc['id']]}\n")
    for c in caps[sc["id"]]["cues"]:
        rows.append((t0 + c["start"], t0 + c["end"], c["text"]))
ts = lambda t, sep: f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}{sep}{int(round((t % 1) * 1000)) % 1000:03d}"
with open(stem + ".srt", "w") as f:
    for i, (a, b, t) in enumerate(rows, 1):
        f.write(f"{i}\n{ts(a, ',')} --> {ts(b, ',')}\n{t}\n\n")
with open(stem + ".vtt", "w") as f:
    f.write("WEBVTT\n\n")
    for a, b, t in rows:
        f.write(f"{ts(a, '.')} --> {ts(b, '.')}\n{t}\n\n")
open(stem + ".transcript.md", "w").write("\n".join(transcript))
print(f"{len(rows)} cues -> {stem}.srt / .vtt / .transcript.md")
