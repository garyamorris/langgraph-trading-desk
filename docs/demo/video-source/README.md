# How the trading_desk walkthrough was made

`../trading-desk-walkthrough.mp4` (1080p) and `-720p.mp4` (for messaging). About 3:40, narrated, with word-timed subtitles.

## What is real, and what is not

* **The run is real.** Scenes C to G and I are a screen-free recording of one live execution of the `trading_desk` graph on a local
  `langgraph dev` server with real models (OpenAI), real Alpaca/Databento data, tracing on. Every number on screen (the TLT $14.5M
  breach, the revised Momentum book, VaR, gross/net, 7 parent orders, TCA) is read from that run's own stream events.
* **Scenes A and B** (structure tour, which node writes which state channel) run on the same page against the same server, but
  nothing executes: they only draw the graph the server reports.
* **Scene H (fail closed) is an illustration.** `REJECTED -> halt_no_trade` did not fire in the recorded run (the LLM CRO agreed with
  the engine and one revision round was enough). The page says so on screen ("illustration", "not taken in the recorded run").
  The branch itself is covered by `tests/test_graph_wiring.py`.

## Pieces

| Piece | Where | What it does |
|---|---|---|
| Live graph page | `viz/desk.html`, `viz/desk.js`, `viz/desk_layout.js`, `viz/lg.js` | Fetches the graph from `GET /assistants/{id}/graph?xray=true`, hand-places the 22 nodes on a large canvas, runs the real graph with `POST /threads/{id}/runs/stream` (modes `tasks`, `checkpoints`, `values`, subgraphs on) and animates the events. A camera (SVG `viewBox`) follows the action so text stays vector-sharp. Modes: `run` (default), `tour`, `state`, `fail`, `replay`, `norun`. |
| Recorder | `rec/rec.swift` -> `bin/rec` | Renders the page in an offscreen `WKWebView` and writes 1920x1080 30 fps H.264 itself, plus a `.markers.json` of timestamped marks (node start/end, camera moves). Build: `swiftc -O -swift-version 5 -suppress-warnings rec/rec.swift -o bin/rec`. |
| Run capture / rehearsal | `capture_run.py` | Runs the graph through the same API and saves every stream event with a timestamp. `viz/desk.html?mode=replay&file=...` replays such a file (LLM waits compressed) so the visuals can be iterated without paying for a model run. |
| Narration | `narration/tts.py`, `narration/script.json` | OpenAI `gpt-4o-mini-tts`, voice `marin`. Initialisms and numbers are respelled for the voice only (`SPOKEN` in `tts.py`); the script and the captions keep the written form. Loudness-normalised to about -16 LUFS. |
| Transcription check + captions | `python3 narration/tts.py transcribe` | Transcribes each final wav with `whisper-1` (word timestamps, with a vocabulary hint, because without it the transcriber hears "LangGraph" as "lane graph"), aligns the heard words to the script, reports mispronunciations, and writes `narration/captions.json`: the script's own spelling, timed to the real audio, at most 44 characters per cue, broken at clause boundaries. `recue` re-chunks offline. |
| Assembly | `make_manifest.py`, `build_video2.py`, FilmCraft | `make_manifest.py` maps each narration scene to a range of the recorded footage using the run's own markers and turns the cues into subtitle callouts. `build_video2.py` drives FilmCraft through its control server (own instance on port 7879, so it never touches another FilmCraft session). |

## Re-making it

```bash
uv run langgraph dev --no-browser --no-reload --port 2024                      # the real graph
(cd docs/demo/video-source && python3 -m http.server 8123 --bind 127.0.0.1)    # serves viz/ and work/
cd docs/demo/video-source
swiftc -O -swift-version 5 -suppress-warnings rec/rec.swift -o bin/rec && cp rec/director_lib.js bin/
./bin/rec --url 'http://127.0.0.1:8123/viz/desk.html?api=http://127.0.0.1:2024&mode=run' \
  --out raw/run.mp4 --director viz/director_wait.js --tail 1.5 --max-seconds 480     # one real run, ~3.5 minutes
./bin/rec --url '...&mode=tour&compact=1&callouts=0' --out raw/tour_a.mp4 --director viz/director_wait.js
./bin/rec --url '...&mode=state&speed=1.5'           --out raw/state_b.mp4 --director viz/director_wait.js
./bin/rec --url '...&mode=fail'                      --out raw/fail_h.mp4  --director viz/director_wait.js
python3 narration/tts.py all && python3 narration/tts.py transcribe             # needs OPENAI_API_KEY in the repo .env
python3 make_manifest.py && python3 build_video2.py manifest_final.json out.mp4 --small
```

After a new run, re-read the real numbers (verdict, desk, breach, VaR, orders) from the thread and update `narration/script.json`
before re-voicing: the script quotes this run's figures.

`raw/` (the 1080p footage, about 100 MB) and `work/` (rehearsals, frames) are not committed.
