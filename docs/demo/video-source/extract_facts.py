#!/usr/bin/env python3
"""Read the real numbers of a recorded run from the LangGraph server (thread state + checkpoint history) and write work/facts.json.
    python3 extract_facts.py THREAD_ID [--api http://127.0.0.1:2024]
make_script.py turns these facts into the narration, so the voice-over never quotes a number the recorded run did not produce."""
import json, sys, urllib.request

tid = sys.argv[1]
api = sys.argv[sys.argv.index("--api") + 1] if "--api" in sys.argv else "http://127.0.0.1:2024"


def get(u, body=None):
    req = urllib.request.Request(api + u, data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))


st = get(f"/threads/{tid}/state")["values"]
hist = get(f"/threads/{tid}/history", {"limit": 100})
verdicts, seen = [], set()
for h in reversed(hist):
    ra = (h["values"] or {}).get("risk_assessment")
    if ra:
        key = (ra["status"], tuple(ra["limit_breaches"]))
        if key not in seen:
            seen.add(key)
            verdicts.append({"status": ra["status"], "breaches": ra["limit_breaches"], "flagged": [d["desk_name"] for d in ra["desk_directives"] if d["must_revise"]],
                             "var": ra["portfolio_var_99_1d_usd"], "gross": ra["combined_gross_exposure_pct_nav"], "net": ra["combined_net_exposure_pct_nav"]})
res = st["adversarial_debate"]["resolution"]
weights = {}
for w in res["desk_allocation_weights_pct"]:
    name, pct = w.split(":")
    weights[name.strip()] = float(pct.strip().rstrip("%"))
head, ex, ra = st["head_of_trading_decision"], st["execution_microstructure_plan"], st["risk_assessment"]
facts = {
    "thread": tid, "tilt": res["winning_tilt"], "weights": weights, "verdicts": verdicts, "revision_count": st["revision_count"],
    "desks": {k: {"gross": v["total_gross_notional_usd"], "legs": [(l["ticker"], l["direction"], l["instrument"], l["notional_usd"]) for l in v["proposed_legs"]]} for k, v in st["desk_proposals"].items()},
    "final": {"status": ra["status"], "gross": ra["combined_gross_exposure_pct_nav"], "net": ra["combined_net_exposure_pct_nav"], "var": ra["portfolio_var_99_1d_usd"], "stressed_var": ra["stressed_var_99_usd"]},
    "head": {"decision": head["overall_decision"], "gross": head["total_deployed_gross_usd"], "net": head["total_deployed_net_usd"]},
    "exec": {"orders": ex["total_parent_orders"], "tca": ex["total_expected_tca_cost_usd"]},
    "n_tickers": len(st["ticker_dossiers"]), "n_intel": len(st["intel_reports"]),
}
json.dump(facts, open("work/facts.json", "w"), indent=1)
print(json.dumps(facts, indent=1)[:1800])
