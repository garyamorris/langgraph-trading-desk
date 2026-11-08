#!/bin/bash
# Re-records every footage clip into raw/: the three structure/state/illustration scenes, then one live run (about 3.5 minutes).
cd "$(dirname "$0")"
URL="http://127.0.0.1:8123/viz/desk.html?api=http://127.0.0.1:2024"
mkdir -p raw work
rec() { name=$1; shift; ./bin/rec --url "$URL&$1" --out raw/$name.mp4 --director viz/director_wait.js --tail ${2:-1} --max-seconds ${3:-120} > work/$name.rec.log 2>&1; echo "$name: $(./bin/vtool info raw/$name.mp4 | head -1 | cut -c1-40) $(grep -o '"console_errors" : [0-9]*' work/$name.rec.log)"; }
rec tour_a  "mode=tour&compact=1&callouts=0"
rec state_b "mode=state&speed=1.5"
rec fail_h  "mode=fail"
rec run     "mode=run" 1.5 480
