#!/usr/bin/env python3
"""OpenAI TTS narration + verified, word-timed captions for the trading_desk walkthrough.

The API key is read from the repo's .env *inside this script*; it is never printed, logged or put on a command line.

USAGE (run from anywhere):
    python3 tts.py models                       list tts-ish models the key can see (GET /v1/models)
    python3 tts.py samples [voice ...]          same 2 sentences in several voices -> narration/samples/<voice>.wav
    python3 tts.py scenes [--voice V] [--only A,B] [--force]
                                                synthesise every scene of script.json -> narration/audio/<id>.wav
                                                (raw takes are cached in narration/cache/ by hash of model|voice|instructions|text)
    python3 tts.py normalise                    trim silence, loudness-normalise to ~-16 LUFS, peak <= -1 dBFS,
                                                write narration/durations.json  ({"A": 12.3, ..., "_total": 245.6})
    python3 tts.py all [--voice V]              scenes + normalise
    python3 tts.py check                        re-measure every audio/<id>.wav (duration, peak, LUFS)
    python3 tts.py transcribe [--only A,B]      round-trip check + captions: transcribe the final wavs with whisper-1 (word timestamps),
                                                align the heard words to the script, report mispronunciations, and write
                                                narration/captions.json (script spelling, word-accurate timing, <= 42 chars per cue)

CONFIG: narration/tts_config.json (created on first run) {"model","voice","instructions","fallback_model"}; change "voice" to
re-voice the whole video (then `python3 tts.py all`).  Pronunciation fixes are spelling tweaks in PRONUNCIATION below; every
applied tweak is recorded in narration/pronunciation_log.json.  script.json is never modified.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import ssl
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", ".env"))
SCRIPT = os.path.join(HERE, "script.json")
CACHE = os.path.join(HERE, "cache")
AUDIO = os.path.join(HERE, "audio")
SAMPLES = os.path.join(HERE, "samples")
CONFIG = os.path.join(HERE, "tts_config.json")
API = "https://api.openai.com/v1"
# python.org builds ship without root certificates; verify against the macOS system bundle (or certifi) instead of disabling checks
for _ca in ("/etc/ssl/cert.pem",):
    if os.path.exists(_ca):
        SSL_CTX = ssl.create_default_context(cafile=_ca)
        break
else:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())

DEFAULT_CONFIG = {
    "model": "gpt-4o-mini-tts",
    "fallback_model": "tts-1-hd",
    "voice": "marin",
    "instructions": (
        "Voice: a warm, wry, intelligent British-leaning storyteller explaining something delightful to developer friends. "
        "Pace: unhurried but engaged, never rushed, with natural pauses between ideas and a slightly longer breath at full stops. "
        "Tone: friendly and conversational with a slight smile, dry humour when it is earned, not a corporate announcer. "
        "Pronunciation: say technical terms clearly and precisely (LangGraph, reducer, checkpoint, interrupt, LangSmith)."
    ),
}

# Spoken forms for tokens a TTS tends to mangle.  The script keeps the *written* form (that is what the captions show);
# only the text sent to the TTS is respelled.  Key = token without surrounding punctuation, lower-case.
SPOKEN = {
    "nvda": "N V D A", "tlt": "T L T", "msft": "M S F T", "amd": "A M D", "qqq": "Q Q Q", "spy": "S P Y", "smh": "S M H",
    "cro": "C R O", "cio": "C I O", "ems": "E M S", "tca": "T C A", "llm": "L L M", "llms": "L L Ms", "api": "A P I",
    "var": "V A R", "thread_id": "thread I D", "merge_dicts": "merge dicts", "add_messages": "add messages",
    "stategraph": "State Graph", "langgraph": "Lang Graph", "langsmith": "lang smith", "msgpack": "message pack",
    "pydantic": "Pie-dantic", "mbp-1": "M B P one", "revise_desks": "revise desks", "halt_no_trade": "halt no trade",
    "10x": "ten times", "uv": "U V", "iex": "I E X",
}
_MONEY = re.compile(r"^\$(\d+(?:\.\d+)?)([MKk])$")
_PCT = re.compile(r"^(\d+(?:\.\d+)?)%$")
_TIMES = re.compile(r"^[x×](\d+)$")


def spoken_of(token: str) -> str:
    m = re.match(r"^([\(\"\u201c]*)(.*?)([\)\"\u201d.,;:!?]*)$", token)
    pre, core, post = m.groups()
    key = core.lower()
    if key in SPOKEN:
        core = SPOKEN[key]
    elif _MONEY.match(core):
        n, u = _MONEY.match(core).groups()
        core = f"{n} {'million' if u == 'M' else 'thousand'} dollars"
    elif _PCT.match(core):
        core = _PCT.match(core).group(1) + " percent"
    elif _TIMES.match(core):
        core = "times " + _TIMES.match(core).group(1)
    return pre + core + post


def spoken_tokens(text: str) -> list[tuple[str, str]]:
    """[(written token, spoken form)] in order."""
    return [(t, spoken_of(t)) for t in text.split()]


SAMPLE_TEXT = (
    "The Triad Risk Committee runs two auditors in parallel, then the C R O reads the engine and rules. "
    "A reducer merges the parallel results into one state."
)
SAMPLE_VOICES = ["marin", "cedar", "fable", "ballad"]


# ---------------------------------------------------------------- plumbing
def api_key() -> str:
    with open(ENV_PATH) as fh:
        for line in fh:
            m = re.match(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("OPENAI_API_KEY not found in .env")


def load_config() -> dict:
    if not os.path.exists(CONFIG):
        json.dump(DEFAULT_CONFIG, open(CONFIG, "w"), indent=2)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.load(open(CONFIG)))
    return cfg


def http(method: str, path: str, body: dict | None = None, raw: bool = False, timeout: float = 180):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer " + api_key())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        detail = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", detail)  # never echo a key fragment
        raise RuntimeError(f"HTTP {exc.code} on {method} {path}: {detail}") from None
    return data if raw else json.loads(data)


def afinfo_duration(path: str) -> float:
    out = subprocess.run(["afinfo", path], capture_output=True, text=True, check=True).stdout
    return float(re.search(r"estimated duration: ([\d.]+) sec", out).group(1))


def apply_pronunciation(text: str, scene_id: str, log: list) -> str:
    out = []
    for written, spoken in spoken_tokens(text):
        if spoken != written:
            log.append({"scene": scene_id, "written": written, "spoken": spoken})
        out.append(spoken)
    return " ".join(out)


def synth(text: str, voice: str, cfg: dict, dest: str, model: str | None = None) -> str:
    """Synthesise (cached) -> 48 kHz 16-bit mono wav at dest. Returns the model actually used."""
    model = model or cfg["model"]
    instr = cfg["instructions"] if model.startswith("gpt-4o") else ""
    key = hashlib.sha1("|".join([model, voice, instr, text]).encode()).hexdigest()[:16]
    os.makedirs(CACHE, exist_ok=True)
    raw = os.path.join(CACHE, f"{key}.wav")
    if not os.path.exists(raw):
        body = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
        if instr:
            body["instructions"] = instr
        t0 = time.time()
        data = http("POST", "/audio/speech", body, raw=True)
        with open(raw + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(raw + ".tmp", raw)
        print(f"  synthesised {os.path.basename(dest)} ({model}/{voice}, {len(text)} chars, {time.time() - t0:.1f}s)")
    else:
        print(f"  cached      {os.path.basename(dest)} ({model}/{voice})")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1", raw, dest], check=True)
    return model


# ---------------------------------------------------------------- commands
def cmd_models(_a) -> None:
    ids = sorted(m["id"] for m in http("GET", "/models")["data"])
    print("\n".join(i for i in ids if "tts" in i or "speech" in i or "audio" in i or "transcribe" in i or "whisper" in i))


def pick_model(cfg: dict) -> dict:
    """Use cfg['model'] if the key can see it, else the best tts model listed."""
    ids = {m["id"] for m in http("GET", "/models")["data"]}
    if cfg["model"] in ids:
        return cfg
    for cand in (cfg.get("fallback_model"), "tts-1-hd", "tts-1"):
        if cand and cand in ids:
            print(f"warning: {cfg['model']} unavailable, falling back to {cand}")
            cfg = dict(cfg, model=cand)
            return cfg
    raise SystemExit("no usable TTS model on this key")


def cmd_samples(a) -> None:
    cfg = pick_model(load_config())
    voices = a.voices or SAMPLE_VOICES
    log: list = []
    text = apply_pronunciation(SAMPLE_TEXT, "sample", log)
    for v in voices:
        try:
            synth(text, v, cfg, os.path.join(SAMPLES, f"{v}.wav"))
        except RuntimeError as exc:
            print(f"  voice {v}: {exc}")
    for f in sorted(os.listdir(SAMPLES)):
        if f.endswith(".wav"):
            print(f"  samples/{f}: {afinfo_duration(os.path.join(SAMPLES, f)):.1f}s")


def cmd_scenes(a) -> None:
    cfg = pick_model(load_config())
    voice = a.voice or cfg["voice"]
    scenes = json.load(open(SCRIPT))["scenes"]
    only = set(a.only.split(",")) if a.only else None
    plog: list = []
    os.makedirs(AUDIO, exist_ok=True)
    used = None
    for sc in scenes:
        if only and sc["id"] not in only:
            continue
        text = apply_pronunciation(sc["text"], sc["id"], plog)
        dest = os.path.join(AUDIO, f"{sc['id']}.wav")
        pre = os.path.join(CACHE, "pre_norm", f"{sc['id']}.wav")
        if os.path.exists(pre):  # a fresh take invalidates the saved un-normalised copy
            os.remove(pre)
        used = synth(text, voice, cfg, dest)
    json.dump({"model": used or cfg["model"], "voice": voice, "instructions": cfg["instructions"], "fixes": plog},
              open(os.path.join(HERE, "pronunciation_log.json"), "w"), indent=2)
    print(f"model={used} voice={voice}; pronunciation fixes applied: {len(plog)}")


# ---------------------------------------------------------------- audio maths (pure python, no numpy)
def read_wav(path: str) -> tuple[array.array, int]:
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, "expected 16-bit mono"
        sr = w.getframerate()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        a.byteswap()
    return a, sr


def write_wav(path: str, samples, sr: int) -> None:
    a = array.array("h", samples)
    if sys.byteorder == "big":
        a.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())


def peak_db(a) -> float:
    pk = max(max(a), -min(a)) if len(a) else 0
    return 20 * math.log10(max(pk, 1) / 32768)


def _biquad(x, b0, b1, b2, a1, a2):
    y = array.array("d", bytes(8 * len(x)))
    x1 = x2 = y1 = y2 = 0.0
    for i, s in enumerate(x):
        o = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[i] = o
        x2, x1, y2, y1 = x1, s, y1, o
    return y


def lufs(a, sr: int = 48000) -> float:
    """ITU-R BS.1770 integrated loudness (mono, K-weighted, 400 ms blocks / 75 % overlap, -70 abs gate, -10 LU rel gate)."""
    if sr != 48000:
        raise ValueError("lufs() expects 48 kHz")
    x = array.array("d", (s / 32768.0 for s in a))
    # stage 1: high shelf, stage 2: RLB high-pass (coefficients for 48 kHz from the standard)
    x = _biquad(x, 1.53512485958697, -2.69169618940638, 1.19839281085285, -1.69065929318241, 0.73248077421585)
    x = _biquad(x, 1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621)
    blk, hop = int(0.4 * sr), int(0.1 * sr)
    if len(x) < blk:
        return -70.0
    sq = array.array("d", (v * v for v in x))
    cs = array.array("d", [0.0]) * (len(sq) + 1)
    acc = 0.0
    for i, v in enumerate(sq):
        acc += v
        cs[i + 1] = acc
    blocks = [(cs[i + blk] - cs[i]) / blk for i in range(0, len(sq) - blk + 1, hop)]
    ldb = lambda z: -0.691 + 10 * math.log10(z) if z > 0 else -200.0
    keep = [z for z in blocks if ldb(z) > -70]
    if not keep:
        return -70.0
    rel = ldb(sum(keep) / len(keep)) - 10
    keep = [z for z in keep if ldb(z) > rel]
    return ldb(sum(keep) / len(keep)) if keep else -70.0


def trim_silence(a, sr: int, head_ms=70, tail_ms=220, thresh_db=-48):
    """Cut leading/trailing near-silence, leaving a little breath, with tiny fades. TTS takes carry ~0.2 s of lead-in."""
    th = int(32768 * 10 ** (thresh_db / 20))
    n = len(a)
    first = next((i for i in range(n) if abs(a[i]) > th), 0)
    last = next((i for i in range(n - 1, -1, -1) if abs(a[i]) > th), n - 1)
    s = max(0, first - int(sr * head_ms / 1000))
    e = min(n, last + 1 + int(sr * tail_ms / 1000))
    out = array.array("h", a[s:e])
    f_in, f_out = int(sr * 0.012), int(sr * 0.06)
    for i in range(min(f_in, len(out))):
        out[i] = int(out[i] * i / f_in)
    for i in range(min(f_out, len(out))):
        out[len(out) - 1 - i] = int(out[len(out) - 1 - i] * i / f_out)
    return out


def apply_gain_limit(a, gain_db: float, ceiling_db: float = -1.2):
    """Gain, then a smooth look-ahead-free peak limiter (instant attack via per-sample gain, 120 ms release) so peaks <= ceiling."""
    g = 10 ** (gain_db / 20)
    ceil = 32768 * 10 ** (ceiling_db / 20)
    out = array.array("h", bytes(2 * len(a)))
    # compute required gain reduction per sample, smooth with a forward-looking min-filter over 2 ms then release
    n = len(a)
    need = array.array("d", [1.0]) * n
    for i, s in enumerate(a):
        v = abs(s) * g
        if v > ceil:
            need[i] = ceil / v
    # spread each reduction 48 samples backward (1 ms look-behind smoothing) and release slowly forward
    rel = math.exp(-1.0 / (0.12 * 48000))
    red = array.array("d", [1.0]) * n
    cur = 1.0
    for i in range(n - 1, -1, -1):  # backward pass: anticipation ramp (attack ~2 ms)
        cur = min(need[i], cur + (1 - cur) * 0.0104 if cur < 1 else 1.0)
        red[i] = cur
    cur = 1.0
    for i in range(n):  # forward pass: release
        cur = min(red[i], 1 - (1 - cur) * rel)
        red[i] = cur
    for i, s in enumerate(a):
        v = s * g * red[i]
        out[i] = max(-32768, min(32767, int(round(v))))
    return out


def cmd_normalise(a) -> None:
    target = -16.0
    ids = [sc["id"] for sc in json.load(open(SCRIPT))["scenes"]]
    results, durs = {}, {}
    for sid in ids:
        src_dir = os.path.join(CACHE, "pre_norm")
        os.makedirs(src_dir, exist_ok=True)
        path = os.path.join(AUDIO, f"{sid}.wav")
        if not os.path.exists(path):
            print(f"  {sid}: missing {path}")
            continue
        pre = os.path.join(src_dir, f"{sid}.wav")
        if not os.path.exists(pre):  # keep the untouched take so normalise is idempotent
            shutil.copy(path, pre)
        x, sr = read_wav(pre)
        x = trim_silence(x, sr)
        l0 = lufs(x, sr)
        gain = target - l0
        y = apply_gain_limit(x, gain)
        l1, pk = lufs(y, sr), peak_db(y)
        write_wav(path, y, sr)
        durs[sid] = round(len(y) / sr, 3)
        results[sid] = (l0, l1, pk, gain)
        print(f"  {sid}: {durs[sid]:6.2f}s  LUFS {l0:6.1f} -> {l1:6.1f}  gain {gain:+5.1f} dB  peak {pk:5.1f} dBFS")
    durs["_total"] = round(sum(v for k, v in durs.items() if k != "_total"), 3)
    json.dump(durs, open(os.path.join(HERE, "durations.json"), "w"), indent=1)
    print(f"total narration {durs['_total']:.1f}s -> durations.json")


def cmd_check(_a) -> None:
    for sid in [sc["id"] for sc in json.load(open(SCRIPT))["scenes"]]:
        p = os.path.join(AUDIO, f"{sid}.wav")
        if not os.path.exists(p):
            print(f"{sid}: MISSING")
            continue
        x, sr = read_wav(p)
        print(f"{sid}: {len(x) / sr:6.2f}s (afinfo {afinfo_duration(p):.2f}s)  sr {sr}  peak {peak_db(x):5.1f} dBFS  LUFS {lufs(x, sr):6.1f}")


def _multipart(fields: list[tuple[str, str]], filename: str, data: bytes) -> tuple[bytes, str]:
    import uuid
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), b


def whisper_words(wav_path: str) -> tuple[str, list[dict]]:
    """whisper-1 with word timestamps -> (full text, [{word,start,end}])."""
    body, boundary = _multipart(
        [("model", "whisper-1"), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"), ("language", "en"),
         ("prompt", VOCAB)],  # vocabulary hint: without it the transcriber hears "LangGraph" as "lane graph" whatever the voice said
        "a.wav", open(wav_path, "rb").read())
    req = urllib.request.Request(API + "/audio/transcriptions", method="POST", data=body)
    req.add_header("Authorization", "Bearer " + api_key())
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=240, context=SSL_CTX).read())
    except urllib.error.HTTPError as exc:
        detail = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", exc.read().decode("utf-8", "replace")[:300])
        raise RuntimeError(f"transcription HTTP {exc.code}: {detail}") from None
    return r.get("text", ""), r.get("words", [])


VOCAB = "LangGraph, LangSmith, Send, subgraph, reducer, TLT, NVDA, CRO, CIO, IEX, VaR, super-step, stat arb, macro vol."
_NORM = lambda s: re.findall(r"[a-z0-9']+", s.lower().replace("-", " "))  # noqa: E731


def align_script_to_audio(text: str, words: list[dict], dur: float):
    """Give every written token of the script a [start, end] from the heard words (difflib alignment on the *spoken* forms).
    Returns (per-token timings, match ratio, list of problems)."""
    import difflib
    toks = spoken_tokens(text)
    spoken_words, owner = [], []
    for ti, (_w, sp) in enumerate(toks):
        for w in _NORM(sp):
            spoken_words.append(w)
            owner.append(ti)
    heard = [(_NORM(w["word"]) or [""])[0] for w in words]
    sm = difflib.SequenceMatcher(None, spoken_words, heard, autojunk=False)
    t_start = [None] * len(spoken_words)
    t_end = [None] * len(spoken_words)
    problems = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                t_start[i1 + k] = words[j1 + k]["start"]
                t_end[i1 + k] = words[j1 + k]["end"]
        elif op == "replace":
            problems.append({"said": " ".join(heard[j1:j2]), "expected": " ".join(spoken_words[i1:i2])})
            n = i2 - i1
            for k in range(n):  # spread over the heard span
                a_, b_ = words[j1]["start"], words[j2 - 1]["end"]
                t_start[i1 + k] = a_ + (b_ - a_) * k / n
                t_end[i1 + k] = a_ + (b_ - a_) * (k + 1) / n
        elif op == "delete":
            problems.append({"said": "", "expected": " ".join(spoken_words[i1:i2])})
        elif op == "insert":
            problems.append({"said": " ".join(heard[j1:j2]), "expected": ""})
    # fill gaps by interpolation between known neighbours
    n = len(spoken_words)
    last = 0.0
    for i in range(n):
        if t_start[i] is None:
            j = i
            while j < n and t_start[j] is None:
                j += 1
            nxt = t_start[j] if j < n else dur
            span = max(0.05, nxt - last)
            cnt = j - i
            for k in range(cnt):
                t_start[i + k] = last + span * k / cnt
                t_end[i + k] = last + span * (k + 1) / cnt
        last = t_end[i]
    per_tok = [[None, None] for _ in toks]
    for i, ti in enumerate(owner):
        if per_tok[ti][0] is None:
            per_tok[ti][0] = t_start[i]
        per_tok[ti][1] = t_end[i]
    prev = 0.0
    for ti, pt in enumerate(per_tok):  # tokens with no spoken words (pure punctuation)
        if pt[0] is None:
            pt[0] = pt[1] = prev
        prev = pt[1]
    ratio = sm.ratio()
    return [(toks[i][0], per_tok[i][0], per_tok[i][1]) for i in range(len(toks))], ratio, problems


BREAK_BEFORE = {"and", "but", "with", "against", "across", "so", "then", "because", "that", "which", "while", "when", "into", "from", "for", "where", "where", "than"}


def build_cues(timed: list, max_chars: int = 44, max_dur: float = 3.4, min_dur: float = 0.9) -> list[dict]:
    """Chunk word timings into short caption cues: break after punctuation, and before conjunctions/prepositions once the line is
    long enough, never beyond max_chars; a short trailing cue is merged back into its predecessor."""
    cues, cur = [], []
    def flush():
        if cur:
            cues.append({"start": round(cur[0][1], 3), "end": round(cur[-1][2], 3), "text": " ".join(w for w, _a, _b in cur)})
            cur.clear()
    for w, a_, b_ in timed:
        line = " ".join(x[0] for x in cur)
        core = re.sub(r"[^a-z']", "", w.lower())
        if cur and (len(line) + 1 + len(w) > max_chars or (b_ - cur[0][1]) > max_dur or (len(line) >= 22 and core in BREAK_BEFORE)):
            flush()
        cur.append((w, a_, b_))
        line = " ".join(x[0] for x in cur)
        if re.search(r"[.!?]$", w) or (re.search(r"[,;:]$", w) and len(line) >= 18):
            flush()
    flush()
    merged = []
    for c in cues:  # a tiny orphan ("end.") reads badly on its own
        if merged and len(c["text"]) <= 14 and len(merged[-1]["text"]) + 1 + len(c["text"]) <= 52 and not re.search(r"[.!?]$", merged[-1]["text"]):
            merged[-1]["text"] += " " + c["text"]; merged[-1]["end"] = c["end"]
        else:
            merged.append(dict(c))
    cues = merged
    # hold each cue until the next begins (no flicker) but not across a real pause; give very short cues a minimum on-screen time
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else c["end"] + 0.4
        c["end"] = round(min(max(c["end"] + 0.12, c["start"] + min_dur), nxt - 0.02 if nxt - c["end"] < 0.7 else c["end"] + 0.35), 3)
        if c["end"] <= c["start"]:
            c["end"] = round(c["start"] + 0.5, 3)
    return cues


def cmd_transcribe(a) -> None:
    """Round-trip check: transcribe each final wav (whisper-1, word timestamps), report words the voice mangled, and write
    captions.json with the SCRIPT's spelling timed to the real audio."""
    scenes = {sc["id"]: sc["text"] for sc in json.load(open(SCRIPT))["scenes"]}
    only = set(a.only.split(",")) if a.only else set(scenes)
    path = os.path.join(HERE, "captions.json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    worst = 1.0
    for sid, text in scenes.items():
        if sid not in only:
            continue
        wav = os.path.join(AUDIO, f"{sid}.wav")
        x, sr = read_wav(wav)
        heard, words = whisper_words(wav)
        timed, ratio, problems = align_script_to_audio(text, words, len(x) / sr)
        cues = build_cues(timed)
        out[sid] = {"duration": round(len(x) / sr, 3), "match": round(ratio, 4), "heard": heard, "problems": problems, "tokens": [[w, round(a_, 3), round(b_, 3)] for w, a_, b_ in timed], "cues": cues}
        worst = min(worst, ratio)
        flag = "" if ratio >= 0.97 else "  <-- check"
        print(f"  {sid}: match {ratio * 100:5.1f}%  {len(cues):2d} cues  problems: {problems or 'none'}{flag}")
    json.dump(out, open(path, "w"), indent=1)
    print(f"captions.json written ({sum(len(v['cues']) for v in out.values())} cues); worst scene match {worst * 100:.1f}%")


def cmd_recue(_a) -> None:
    """Rebuild caption cues from the stored word timings (no API call) after tweaking build_cues()."""
    path = os.path.join(HERE, "captions.json")
    out = json.load(open(path))
    for sid, v in out.items():
        v["cues"] = build_cues([(w, a_, b_) for w, a_, b_ in v["tokens"]])
        print(f"  {sid}: {len(v['cues'])} cues")
    json.dump(out, open(path, "w"), indent=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    s = sub.add_parser("samples")
    s.add_argument("voices", nargs="*")
    for name in ("scenes", "all"):
        s = sub.add_parser(name)
        s.add_argument("--voice")
        s.add_argument("--only")
        s.add_argument("--force", action="store_true")
    sub.add_parser("normalise")
    sub.add_parser("check")
    sub.add_parser("recue")
    s = sub.add_parser("transcribe")
    s.add_argument("--only")
    a = ap.parse_args()
    try:
        if a.cmd == "models":
            cmd_models(a)
        elif a.cmd == "samples":
            cmd_samples(a)
        elif a.cmd == "scenes":
            cmd_scenes(a)
        elif a.cmd == "normalise":
            cmd_normalise(a)
        elif a.cmd == "all":
            cmd_scenes(a)
            cmd_normalise(a)
        elif a.cmd == "check":
            cmd_check(a)
        elif a.cmd == "transcribe":
            cmd_transcribe(a)
        elif a.cmd == "recue":
            cmd_recue(a)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
