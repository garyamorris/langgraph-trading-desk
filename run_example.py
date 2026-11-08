"""CLI runner to execute and stream the 24-Node Hierarchical Multi-Strategy Hedge Fund workflow."""

from __future__ import annotations

import sys
from langchain_core.messages import HumanMessage
from trading_desk.graph import graph


def main() -> None:
    prompt = (
        sys.argv[1]
        if len(sys.argv) > 1
        else (
            "Run our full 24-node institutional trading workflow across NVDA, AMD, MSFT, SPY, QQQ, and TLT: "
            "1) Dispatch the 6-agent Intelligence Syndicate + dynamic Send() single-ticker deep research workers; "
            "2) Run the Adversarial Bull vs Bear Debate; "
            "3) Execute all 4 Strategy Desk Subgraphs (Momentum pitching a $15M NVDA lead trade, Stat-Arb Cointegration Pairs, "
            "Macro/Vol Tail Overlay, and Event/Catalyst Arb); "
            "4) Run the Triad Risk & Monte Carlo Stress Committee with selective desk re-dispatch until approved; "
            "5) Generate the Global Head of Trading master blotter and Microstructure EMS child-order slicing plan."
        )
    )

    print("=" * 105)
    print("🏛️  STARTING 24-NODE HIERARCHICAL MULTI-STRATEGY HEDGE FUND (SUBGRAPHS + SEND() MAP-REDUCE + DEBATE)")
    print(f"Directive: {prompt}")
    print("=" * 105)

    initial_state = {
        "messages": [HumanMessage(content=prompt)],
        "focus_tickers": ["NVDA", "AMD", "MSFT", "SPY", "QQQ", "TLT"],
        "cio_mandate": None,
        "intel_reports": {},
        "ticker_dossiers": {},
        "adversarial_debate": {},
        "desk_proposals": {},
        "risk_sub_reports": {},
        "risk_assessment": None,
        "head_of_trading_decision": None,
        "execution_microstructure_plan": None,
        "revision_count": 0,
        "max_revisions": 2,
    }

    for event in graph.stream(initial_state, stream_mode="updates"):
        for node_name, update in event.items():
            if not isinstance(update, dict):
                continue
            msgs = update.get("messages", [])
            if not msgs:
                if "ticker_dossiers" in update:
                    tickers_done = list(update["ticker_dossiers"].keys())
                    print(f"  ⚡ [Send() Map-Reduce Worker Completed Dossier]: {tickers_done}")
                elif "risk_sub_reports" in update:
                    sub_keys = list(update["risk_sub_reports"].keys())
                    print(f"  🛡️ [Parallel Triad Risk Auditor Completed]: {sub_keys}")
                else:
                    print(f"  ⚡ [Node Completed: {node_name}]")
                continue
            print(f"\n{'═' * 95}\n✅ Node Completed: [{node_name}]\n{'═' * 95}")
            for m in msgs:
                print(m.content)


if __name__ == "__main__":
    main()
