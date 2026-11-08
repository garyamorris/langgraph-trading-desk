#!/usr/bin/env python3
"""Write narration/script.json from work/facts.json (see extract_facts.py), so every figure the voice quotes is one the recorded run
produced.  Scenes that quote no run-specific number are fixed text.  Fails loudly if the run does not have the story the script tells
(a first REVISE_DESKS verdict, then APPROVED)."""
import json, re, sys

f = json.load(open("work/facts.json"))
v = f["verdicts"]
if not (len(v) >= 2 and v[0]["status"] == "REVISE_DESKS" and v[-1]["status"] == "APPROVED"):
    sys.exit(f"this run's verdicts are {[x['status'] for x in v]}: the script tells a REVISE_DESKS -> APPROVED story; record another run")
NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
words = lambda n: NUM.get(n, str(n))
pct = lambda x: f"{x:g}%"
usd_m = lambda x: "$" + (f"{x / 1e6:.1f}".rstrip("0").rstrip(".")) + "M"
desk = lambda d: d.replace("_DESK", "").replace("_", " ").lower()
joined = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

w = f["weights"]
tilt = f["tilt"].replace("_", " ").lower()
first = v[0]
flagged = [desk(d) for d in first["flagged"]]
m = re.search(r"\[(\w+)\] (\w+) single-leg notional \(\$([\d,]+)\) exceeds cap of \$([\d,]+)", first["breaches"][0]) if first["breaches"] else None
if m:
    breach = f"a {m.group(2)} position of {usd_m(int(m.group(3).replace(',', '')))}, against a {usd_m(int(m.group(4).replace(',', '')))} cap"
else:
    breach = re.sub(r"^\[\w+\]\s*", "", first["breaches"][0]).rstrip(".") if first["breaches"] else "a limit breach"
who = f"the {joined(flagged)} desk" + ("s" if len(flagged) > 1 else "")
if len(first["breaches"]) > 1:
    who += f"; the first breach is {breach}"
    flag_sentence = f"Here it flags {who}."
else:
    flag_sentence = f"Here it flags {who}: {breach}."
rev_desks = first["flagged"]
if len(rev_desks) == 1:
    comes = f"{desk(rev_desks[0]).capitalize()} comes back with a book of {usd_m(f['desks'][rev_desks[0]]['gross'])} gross"
    only = "only the offending desk"
else:
    comes = "The flagged desks come back with smaller books"
    only = "only the offending desks"
fin = f["final"]
decision = {"EXECUTE_SCALED_AND_HEDGED": "scaled and hedged", "EXECUTE_ALL": "executed in full"}.get(f["head"]["decision"], f["head"]["decision"].lower().replace("_", " "))
rounds = f["revision_count"]

T = {
 "A": "This is a hedge fund trading desk, built as a single LangGraph: nineteen nodes, with six analysts, a bull and a bear, four strategy desks, a risk committee, and an execution router. Everything you are about to see is a real run, recorded against the live graph with real models.",
 "B": "Nodes never call each other. They share one typed state, thirteen channels. Each node reads what it needs and returns only the keys it changed. When parallel branches write to the same channel, a reducer decides how to combine them: five channels here merge dictionaries, so six analysts can write at once without overwriting each other.",
 "C": f"The run starts at the CIO router, which fans out in two ways. Six analysts start at once. And a Send call spawns one deep research worker per ticker: the same function, mapped over {words(f['n_tickers'])} inputs. They all run in one super-step, so the aggregator fires only when the slowest branch lands, and the reducers have merged {words(f['n_intel'] + f['n_tickers'])} parallel writes into the state.",
 "D": f"The aggregator feeds two nodes that run in parallel: a bull advocate and a bear critic, reading the same state and arguing opposing cases. Then the arbiter rules. In this run it settled on {tilt}, and split the capital: {pct(w['MOMENTUM_DESK'])} momentum, {pct(w['STAT_ARB_DESK'])} stat arb, {pct(w['MACRO_VOL_DESK'])} macro vol, {pct(w['EVENT_ARB_DESK'])} event arb.",
 "E": "Next, the desks. The arbiter returns one Send per desk, but there is only one desk subgraph, compiled once with its own input and output schemas, and run four times in parallel. A quant models the legs, a desk head signs off, and the quant's draft stays in a private channel. Only the final proposal flows back to shared state.",
 "F": f"Now the gate. Two auditors run in parallel, and the chief risk officer reads them. But the language model does not get the final say: a deterministic risk engine computes exposure and value at risk, and its verdict is authoritative. {flag_sentence}",
 "G": f"The conditional edge returns the verdict revise, and the router sends {only} around again, with a fresh Send. The revision counter moves from zero to {words(rounds)}. {comes}, the engine re-checks, and this time it approves: about {pct(fin['gross'])} gross, {pct(fin['net'])} net, value at risk under the limit.",
 "H": "The loop is bounded to two rounds. If breaches are still open after that, the graph does not approve by default: a rejected verdict routes to a halt node, and the run ends with no trade. That branch did not fire in this recording; the test suite covers it.",
 "I": f"Approved, so the head of trading releases the blotter: {usd_m(f['head']['gross'])} gross, {decision}. The execution router slices {words(f['exec']['orders'])} parent orders into child orders across dark pools, IEX and lit venues, with about ${round(f['exec']['tca'] / 1000)}k of estimated cost, and the run reaches its end.",
 "K": "One shared state, parallel fan-outs, a subgraph reused four times, a deterministic gate with a bounded loop, and a fail-closed exit. Run it with langgraph dev and watch it in Studio. It is a demo on paper data, and it is not trading advice.",
}
json.dump({"scenes": [{"id": k, "text": t} for k, t in T.items()]}, open("narration/script.json", "w"), indent=1)
for k in "DFGI":
    print(k, "|", T[k])
