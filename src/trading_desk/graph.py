"""24-Node Hierarchical Multi-Strategy Hedge Fund built with LangGraph, Subgraphs, Send() Map-Reduce, OpenAI, Alpaca, and Databento."""

from __future__ import annotations

import json
import os
import re
from typing import Any, get_args

from dotenv import load_dotenv

load_dotenv(override=True)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel

from trading_desk.decisions_engine import (
    evaluate_cro_gate_via_decisions_api,
    evaluate_ticker_via_decisions_api,
    judge_debate_via_decisions_api,
    select_ems_algos_via_decisions_api,
)
from trading_desk.prompts import (
    BEAR_SHORT_SELLER_PROMPT,
    BULL_DIRECTOR_PROMPT,
    CIO_ROUTER_PROMPT,
    CRO_SYNTHESIS_PROMPT,
    DEBATE_ARBITER_PROMPT,
    DESK_HEAD_SUBGRAPH_PROMPT,
    DESK_QUANT_PROMPT,
    EMS_MICROSTRUCTURE_ROUTER_PROMPT,
    FACTOR_COINTEGRATION_PROMPT,
    FACTOR_EXPOSURE_AUDITOR_PROMPT,
    HEAD_OF_TRADING_PROMPT,
    MACRO_STRATEGIST_PROMPT,
    NEWS_SENTIMENT_PROMPT,
    PREMARKET_SCANNER_PROMPT,
    TAIL_VAR_MONTE_CARLO_PROMPT,
    TECHNICAL_ANALYST_PROMPT,
    TICKER_DEEP_WORKER_PROMPT,
    VOL_SURFACE_PROMPT,
)
from trading_desk.state import (
    AdversarialDebateResolution,
    ApprovedExecutionTicket,
    ChildOrderSliceSchedule,
    CIORegimeMandate,
    DebateArgument,
    DeskCapitalAllocation,
    DeskInput,
    DeskMandateProposal,
    DeskName,
    DeskOutput,
    DeskQuantModelOutput,
    DeskRiskDirective,
    DeskState,
    ExecutionMicrostructurePlan,
    FactorCointegrationReport,
    FactorExposureAudit,
    HeadOfTradingDecision,
    MacroRegimeReport,
    NewsSentimentReport,
    PortfolioRiskReport,
    PremarketBrief,
    TailRiskMonteCarloReport,
    TechnicalSignalReport,
    TickerDeepResearchDossier,
    TickerWorkerState,
    TradingDeskState,
    VolSurfaceReport,
)
from trading_desk.tools import (
    COINTEGRATION_TOOLS,
    MACRO_TOOLS,
    NEWS_TOOLS,
    PREMARKET_TOOLS,
    SECTOR_MAP,
    TECHNICAL_TOOLS,
    VOL_SURFACE_TOOLS,
    analyze_volatility_surface_and_skew,
    compute_correlation_and_cointegration_matrix,
    compute_technical_indicators,
    evaluate_multi_desk_book_risk,
    fetch_alpaca_live_snapshots,
    fetch_alpaca_news_sentiment,
    fetch_cross_asset_macro_regime,
    fetch_databento_orderbook_microstructure,
    get_firm_portfolio_and_mandate,
)

load_dotenv()

TOOL_MAP = {
    "fetch_alpaca_live_snapshots": fetch_alpaca_live_snapshots,
    "fetch_databento_orderbook_microstructure": fetch_databento_orderbook_microstructure,
    "compute_technical_indicators": compute_technical_indicators,
    "fetch_cross_asset_macro_regime": fetch_cross_asset_macro_regime,
    "fetch_alpaca_news_sentiment": fetch_alpaca_news_sentiment,
    "analyze_volatility_surface_and_skew": analyze_volatility_surface_and_skew,
    "compute_correlation_and_cointegration_matrix": compute_correlation_and_cointegration_matrix,
    "get_firm_portfolio_and_mandate": get_firm_portfolio_and_mandate,
    "evaluate_multi_desk_book_risk": evaluate_multi_desk_book_risk,
}

DEFAULT_UNIVERSE = ["NVDA", "AMD", "MSFT", "SPY", "QQQ", "TLT"]
ALL_DESKS: list[str] = list(get_args(DeskName))
DESK_NODE = "strategy_desk"  # one compiled subgraph, dispatched once per desk via Send()


def _get_llm(temperature: float | None = None) -> ChatOpenAI:
    """Instantiate `gpt-6-luna` (without temperature, as Luna does not support temperature)."""
    model_name = os.getenv("OPENAI_MODEL", "gpt-6-luna")
    if "luna" in model_name.lower():
        return ChatOpenAI(model=model_name)
    return ChatOpenAI(model=model_name, temperature=0.1 if temperature is None else temperature)


def _model_default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _json(obj: Any) -> str:
    """Pretty JSON for prompt context. State holds Pydantic models, so dump them on the way out."""
    return json.dumps(obj, indent=2, default=_model_default)


def _extract_focus_tickers(state: TradingDeskState) -> list[str]:
    """Extract tickers from state or user prompt, ensuring a rich multi-strategy universe."""
    existing = state.get("focus_tickers")
    if existing and len(existing) >= 3:
        return existing[:6]

    user_text = " ".join(
        str(m.content) for m in state.get("messages", []) if isinstance(m.content, str)
    ).upper()
    detected = [sym for sym in SECTOR_MAP if re.search(rf"\b{sym}\b", user_text)]
    merged = list(dict.fromkeys(detected + DEFAULT_UNIVERSE))
    return merged[:6]


def _run_tool_calling_loop(
    llm_with_tools,
    messages: list,
    max_tool_rounds: int = 2,
) -> list:
    """Execute tool calls requested by the agent and append ToolMessages."""
    working_messages = list(messages)
    for _ in range(max_tool_rounds):
        response = llm_with_tools.invoke(working_messages)
        working_messages.append(response)
        if not getattr(response, "tool_calls", None):
            break
        for tc in response.tool_calls:
            tool_fn = TOOL_MAP.get(tc["name"])
            if tool_fn is None:
                tool_output = {"error": f"Unknown tool: {tc['name']}"}
            else:
                try:
                    tool_output = tool_fn.invoke(tc["args"])
                except Exception as exc:
                    tool_output = {"error": str(exc)}
            working_messages.append(
                ToolMessage(
                    content=json.dumps(tool_output, indent=2),
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
    return working_messages


# ============================================================================
# TIER 0: CIO REGIME ROUTER & DYNAMIC SEND() FAN-OUT
# ============================================================================

def cio_regime_router_node(state: TradingDeskState) -> dict[str, Any]:
    """Chief Investment Officer node: parses directive, sets capital budget, and selects focus tickers."""
    llm = _get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(CIORegimeMandate)
    tickers = _extract_focus_tickers(state)

    user_requests = [
        m.content for m in state.get("messages", []) if isinstance(m, HumanMessage)
    ]
    directive_text = user_requests[-1] if user_requests else "Deploy multi-strategy capital across focus universe."

    mandate: CIORegimeMandate = structured_llm.invoke(
        [
            SystemMessage(content=CIO_ROUTER_PROMPT),
            HumanMessage(
                content=(
                    f"User Directive: {directive_text}\n"
                    f"Candidate Universe: {tickers}\n"
                    "Produce the `CIORegimeMandate`."
                )
            ),
        ]
    )
    chosen_tickers = [t.upper().strip() for t in mandate.focus_tickers if t.strip()][:6] or tickers

    msg = (
        f"### 🏛️ Tier 0: CIO Global Regime Mandate — `{mandate.session_regime}`\n"
        f"- **Selected Focus Universe (`Send()` Map-Reduce)**: `{', '.join(chosen_tickers)}`\n"
        f"- **Activated Strategy Desks**: `{', '.join(mandate.active_desks)}`\n"
        f"- **Target Gross Capital Utilization**: `{mandate.target_firm_gross_utilization_pct:.1f}%` of $100M NAV\n"
        f"- **CIO Strategic Priorities**: {mandate.strategic_priorities}"
    )
    return {
        "focus_tickers": chosen_tickers,
        "cio_mandate": mandate,
        "revision_count": state.get("revision_count") or 0,
        "max_revisions": state.get("max_revisions") or 2,
        "messages": [AIMessage(content=msg, name="CIO_Regime_Router")],
    }


def fan_out_from_cio_router(state: TradingDeskState) -> list[str | Send]:
    """Simultaneously trigger all 6 Intelligence Syndicate agents AND spawn N dynamic Send() ticker workers."""
    tickers = _extract_focus_tickers(state)
    static_branches: list[str | Send] = [
        "premarket_microstructure",
        "technical_analyst",
        "macro_strategist",
        "news_sentiment",
        "vol_surface_analyst",
        "factor_cointegration_analyst",
    ]
    # Dynamic Map-Reduce: spawn one parallel worker execution per ticker
    dynamic_sends = [
        Send("ticker_deep_research_worker", {"ticker": sym}) for sym in tickers[:4]
    ]
    return static_branches + dynamic_sends


# ============================================================================
# TIER 1: 6-WAY PARALLEL INTELLIGENCE SYNDICATE + SEND() TICKER WORKER
# ============================================================================

def premarket_microstructure_node(state: TradingDeskState) -> dict[str, Any]:
    """Pre-Market & Databento Orderbook Microstructure Analyst node."""
    tickers = _extract_focus_tickers(state)
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(PREMARKET_TOOLS)
    structured_llm = llm.with_structured_output(PremarketBrief)

    local_messages = [
        SystemMessage(content=PREMARKET_SCANNER_PROMPT),
        HumanMessage(
            content=(
                f"Focus tickers: {tickers}.\n"
                "Call `fetch_alpaca_live_snapshots` AND `fetch_databento_orderbook_microstructure`, "
                "then output the `PremarketBrief`."
            )
        ),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `PremarketBrief`."))
    brief: PremarketBrief = structured_llm.invoke(local_messages)

    msg = (
        f"### 🌅 Pre-Market & Databento L1 Microstructure Brief\n"
        f"- **Session**: `{brief.market_session}` | **Bias**: `{brief.conviction_bias}`\n"
        f"- **Overnight & Tape Gaps (Alpaca)**: {brief.overnight_gaps_summary}\n"
        f"- **Orderbook Microstructure (Databento `mbp-1`)**: {brief.microstructure_summary}"
    )
    return {
        "intel_reports": {"premarket": brief},
        "messages": [AIMessage(content=msg, name="Premarket_Microstructure_Analyst")],
    }


def technical_analyst_node(state: TradingDeskState) -> dict[str, Any]:
    """Quantitative Technical Analyst node."""
    tickers = _extract_focus_tickers(state)
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(TECHNICAL_TOOLS)
    structured_llm = llm.with_structured_output(TechnicalSignalReport)

    local_messages = [
        SystemMessage(content=TECHNICAL_ANALYST_PROMPT),
        HumanMessage(
            content=f"Focus tickers: {tickers}. Call `compute_technical_indicators` and produce `TechnicalSignalReport`."
        ),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `TechnicalSignalReport`."))
    report: TechnicalSignalReport = structured_llm.invoke(local_messages)

    sig_lines = "\n".join(
        f"  - **{item.ticker}** (`{item.technical_regime}`): {item.signal_summary}"
        for item in report.ticker_signals
    )
    msg = (
        f"### 📈 Quantitative Technical Analysis (`{report.volatility_regime}`)\n"
        f"- **Top Momentum Breakout**: {report.top_momentum_long}\n"
        f"- **Top Mean-Reversion / Short Setup**: {report.top_mean_reversion_or_short}\n"
        f"- **Signals**:\n{sig_lines}"
    )
    return {
        "intel_reports": {"technicals": report},
        "messages": [AIMessage(content=msg, name="Quant_Technical_Analyst")],
    }


def macro_strategist_node(state: TradingDeskState) -> dict[str, Any]:
    """Cross-Asset Macro & Factor Rotation Strategist node."""
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(MACRO_TOOLS)
    structured_llm = llm.with_structured_output(MacroRegimeReport)

    local_messages = [
        SystemMessage(content=MACRO_STRATEGIST_PROMPT),
        HumanMessage(content="Call `fetch_cross_asset_macro_regime` and produce `MacroRegimeReport`."),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `MacroRegimeReport`."))
    report: MacroRegimeReport = structured_llm.invoke(local_messages)

    msg = (
        f"### 🌍 Cross-Asset Macro & Factor Regime — `{report.macro_regime}`\n"
        f"- **Risk Appetite Scalar**: `{report.recommended_gross_leverage_scalar:.2f}x`\n"
        f"- **Leaders**: {', '.join(report.sector_leadership)} | **Laggards**: {', '.join(report.sector_laggards)}\n"
        f"- **Rates, Credit & Dollar View**: {report.rates_and_dollar_view}"
    )
    return {
        "intel_reports": {"macro": report},
        "messages": [AIMessage(content=msg, name="Global_Macro_Strategist")],
    }


def news_sentiment_node(state: TradingDeskState) -> dict[str, Any]:
    """Real-Time Alpaca Breaking News & Sentiment Analyst node."""
    tickers = _extract_focus_tickers(state)
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(NEWS_TOOLS)
    structured_llm = llm.with_structured_output(NewsSentimentReport)

    local_messages = [
        SystemMessage(content=NEWS_SENTIMENT_PROMPT),
        HumanMessage(content=f"Focus tickers: {tickers}. Call `fetch_alpaca_news_sentiment` and produce `NewsSentimentReport`."),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `NewsSentimentReport`."))
    report: NewsSentimentReport = structured_llm.invoke(local_messages)

    scores_str = ", ".join(
        f"**{item.ticker}**: `{item.sentiment_score:+.2f}`" for item in report.ticker_sentiments
    )
    headlines_md = "\n".join(f"  - 📰 {h}" for h in report.key_catalysts_and_headlines[:3])
    msg = (
        f"### 🗞️ Real-Time Alpaca News & Catalyst Sentiment — `{report.overall_sentiment}`\n"
        f"- **Ticker Scores**: {scores_str}\n"
        f"- **Breaking Headlines**:\n{headlines_md}"
    )
    return {
        "intel_reports": {"news": report},
        "messages": [AIMessage(content=msg, name="News_Sentiment_Analyst")],
    }


def vol_surface_analyst_node(state: TradingDeskState) -> dict[str, Any]:
    """Options Volatility Surface, Skew & Dealer Gamma Analyst node."""
    tickers = _extract_focus_tickers(state)
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(VOL_SURFACE_TOOLS)
    structured_llm = llm.with_structured_output(VolSurfaceReport)

    local_messages = [
        SystemMessage(content=VOL_SURFACE_PROMPT),
        HumanMessage(
            content=f"Focus tickers: {tickers}. Call `analyze_volatility_surface_and_skew` and produce `VolSurfaceReport`."
        ),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `VolSurfaceReport`."))
    report: VolSurfaceReport = structured_llm.invoke(local_messages)

    vol_lines = "\n".join(
        f"  - **{v.ticker}**: IV-RV Spread `{v.iv_rv_spread_vol_pts:+.2f} pts` | Skew: `{v.put_call_skew_regime}` -> `{v.optimal_options_structure}`"
        for v in report.ticker_vol_signals
    )
    msg = (
        f"### 🌊 Options Volatility Surface & Dealer Gamma — `{report.dealer_gamma_regime}`\n"
        f"- **Top Vol Arbitrage / Convexity Setup**: {report.top_vol_arbitrage_setup}\n"
        f"- **Vol Surface Readouts**:\n{vol_lines}"
    )
    return {
        "intel_reports": {"vol_surface": report},
        "messages": [AIMessage(content=msg, name="Vol_Surface_Gamma_Analyst")],
    }


def factor_cointegration_analyst_node(state: TradingDeskState) -> dict[str, Any]:
    """Cross-Sectional Correlation, PCA Crowding & Stat-Arb Cointegration Analyst node."""
    tickers = _extract_focus_tickers(state)
    llm = _get_llm(temperature=0.1)
    llm_with_tools = llm.bind_tools(COINTEGRATION_TOOLS)
    structured_llm = llm.with_structured_output(FactorCointegrationReport)

    local_messages = [
        SystemMessage(content=FACTOR_COINTEGRATION_PROMPT),
        HumanMessage(
            content=f"Focus tickers: {tickers}. Call `compute_correlation_and_cointegration_matrix` and produce `FactorCointegrationReport`."
        ),
    ]
    local_messages = _run_tool_calling_loop(llm_with_tools, local_messages)
    local_messages.append(HumanMessage(content="Output the structured `FactorCointegrationReport`."))
    report: FactorCointegrationReport = structured_llm.invoke(local_messages)

    pair_lines = "\n".join(
        f"  - **LONG {p.long_leg} / SHORT {p.short_leg}**: 60d Corr `{p.rolling_correlation_60d:.2f}` | Spread Z-Score `{p.spread_z_score_20d:.2f}σ` | Half-Life `{p.half_life_days:.1f}d`"
        for p in report.top_cointegrated_pairs
    )
    msg = (
        f"### 🧮 Cross-Sectional Factor & Cointegration Matrix (PC1 Variance: `{report.dominant_pca_factor_explained_pct:.1f}%`)\n"
        f"- **Crowding Diagnosis**: {report.crowding_warning}\n"
        f"- **Top Cointegrated Stat-Arb Pairs**:\n{pair_lines}"
    )
    return {
        "intel_reports": {"cointegration": report},
        "messages": [AIMessage(content=msg, name="Factor_Cointegration_Analyst")],
    }


def ticker_deep_research_worker(state: TickerWorkerState) -> dict[str, Any]:
    """Dynamic Map-Reduce worker spawned via Send() for deep single-ticker synthesis + OpenAI Decisions API scoring."""
    ticker = state["ticker"].upper().strip()
    llm = _get_llm()
    structured_llm = llm.with_structured_output(TickerDeepResearchDossier)

    snap = fetch_alpaca_live_snapshots.invoke({"tickers": [ticker]})
    tech = compute_technical_indicators.invoke({"tickers": [ticker]})
    micro = fetch_databento_orderbook_microstructure.invoke({"tickers": [ticker]})

    evidence_str = (
        f"Assigned Ticker: {ticker}\n"
        f"Live Alpaca Snapshot:\n{json.dumps(snap, indent=2)}\n"
        f"Quantitative Technicals:\n{json.dumps(tech, indent=2)}\n"
        f"Databento L1 Microstructure:\n{json.dumps(micro, indent=2)}"
    )

    # Call OpenAI Decisions API (client.decisions.create with gpt-6-luna) for calibrated probabilities
    decisions_eval = evaluate_ticker_via_decisions_api(ticker=ticker, evidence_text=evidence_str)

    dossier: TickerDeepResearchDossier = structured_llm.invoke(
        [
            SystemMessage(content=TICKER_DEEP_WORKER_PROMPT),
            HumanMessage(
                content=(
                    f"{evidence_str}\n\n"
                    f"OpenAI Decisions API (`gpt-6-luna`) Probabilistic Evaluation:\n"
                    f"{json.dumps(decisions_eval, indent=2)}\n\n"
                    "Produce the `TickerDeepResearchDossier` aligned with the Decisions API probabilities."
                )
            ),
        ]
    )
    dossier.ticker = ticker
    dossier.openai_decisions_api = decisions_eval["answers"]
    return {
        "ticker_dossiers": {ticker: dossier},
    }


def intelligence_aggregator_node(state: TradingDeskState) -> dict[str, Any]:
    """Synchronization barrier that merges all 6 Intelligence Syndicate feeds + N dynamic Send() Ticker Dossiers."""
    dossiers = state.get("ticker_dossiers") or {}
    lines = []
    for sym, d in dossiers.items():
        dec = d.openai_decisions_api
        flow_prob = dec.get("institutional_orderflow_supportive", {}).get("probability", 0.5)
        tail_prob = dec.get("event_or_tail_risk_elevated", {}).get("probability", 0.5)
        conv_score = dec.get("setup_conviction_tier", {}).get("score", 0.0)
        lines.append(
            f"  - **{sym}** (Alpha `{d.composite_alpha_score:+.1f}/10` | Spot `${d.live_spot_price:.2f}` | OBI `{d.microstructure_obi:+.3f}`): "
            f"`{d.preferred_direction}` via `{d.preferred_instrument}` "
            f"(Target `${d.suggested_target:.2f}`, Stop `${d.suggested_stop_within_7pct:.2f}`) "
            f"— **Decisions API (`gpt-6-luna`)**: Flow Supportive `{flow_prob:.0%}`, Tail Risk `{tail_prob:.0%}`, Conviction Score `{conv_score:.2f}`"
        )
    dossier_md = "\n".join(lines) if lines else "  - Dossiers merged."
    msg = (
        f"### ⚡ Tier-1 Intelligence Syndicate & `Send()` Map-Reduce Dossiers Synchronized\n"
        f"- **Merged Single-Name Dossiers ({len(dossiers)} Parallel Workers + OpenAI Decisions API)**:\n{dossier_md}"
    )
    return {
        "messages": [AIMessage(content=msg, name="Intelligence_Aggregator")],
    }


# ============================================================================
# TIER 2: ADVERSARIAL BULL vs BEAR INVESTMENT COMMITTEE DEBATE
# ============================================================================

def _debate_context(state: TradingDeskState) -> str:
    return (
        f"CIO Mandate:\n{_json(state.get('cio_mandate'))}\n\n"
        f"Tier-1 Intelligence Syndicate Reports:\n{_json(state.get('intel_reports'))}\n\n"
        f"Single-Name Deep Research Dossiers:\n{_json(state.get('ticker_dossiers'))}"
    )


def bull_thesis_advocate_node(state: TradingDeskState) -> dict[str, Any]:
    """Bullish Alpha & Secular Growth Research Director node."""
    llm = _get_llm(temperature=0.2)
    structured_llm = llm.with_structured_output(DebateArgument)
    arg: DebateArgument = structured_llm.invoke(
        [
            SystemMessage(content=BULL_DIRECTOR_PROMPT),
            HumanMessage(content=_debate_context(state)),
        ]
    )
    arg.speaker = "BULL_RESEARCH_DIRECTOR"
    trades_md = "\n".join(f"  - 🟢 {t}" for t in arg.high_conviction_trades)
    msg = (
        f"### 🐂 Adversarial Debate — Bull Research Director\n"
        f"- **Core Bull Thesis**: {arg.core_thesis}\n"
        f"- **High-Conviction Bull Trades**:\n{trades_md}\n"
        f"- **Critique of Bear Case**: {arg.critique_of_opponent}"
    )
    return {
        "adversarial_debate": {"bull_case": arg},
        "messages": [AIMessage(content=msg, name="Bull_Research_Director")],
    }


def bear_thesis_critic_node(state: TradingDeskState) -> dict[str, Any]:
    """Forensic Short-Selling & Tail-Risk Red-Team Director node."""
    llm = _get_llm(temperature=0.2)
    structured_llm = llm.with_structured_output(DebateArgument)
    arg: DebateArgument = structured_llm.invoke(
        [
            SystemMessage(content=BEAR_SHORT_SELLER_PROMPT),
            HumanMessage(content=_debate_context(state)),
        ]
    )
    arg.speaker = "BEAR_SHORT_SELLER"
    trades_md = "\n".join(f"  - 🔴 {t}" for t in arg.high_conviction_trades)
    msg = (
        f"### 🐻 Adversarial Debate — Forensic Bear Short-Seller\n"
        f"- **Core Bear / Risk Thesis**: {arg.core_thesis}\n"
        f"- **High-Conviction Short & Hedge Trades**:\n{trades_md}\n"
        f"- **Critique of Bull Case**: {arg.critique_of_opponent}"
    )
    return {
        "adversarial_debate": {"bear_case": arg},
        "messages": [AIMessage(content=msg, name="Bear_Short_Seller")],
    }


def adversarial_debate_arbiter_node(state: TradingDeskState) -> dict[str, Any]:
    """CIO Debate Arbiter node: uses OpenAI Decisions API (`gpt-6-luna`) + structured synthesis to resolve Bull vs Bear debate."""
    llm = _get_llm()
    structured_llm = llm.with_structured_output(AdversarialDebateResolution)
    debate = state.get("adversarial_debate") or {}

    debate_evidence = (
        f"{_debate_context(state)}\n\n"
        f"Bull Director Argument:\n{_json(debate.get('bull_case'))}\n\n"
        f"Bear Short-Seller Argument:\n{_json(debate.get('bear_case'))}"
    )

    # Call OpenAI Decisions API (client.decisions.create) to score Bull vs Bear rigor & select winning tilt
    decisions_judge = judge_debate_via_decisions_api(debate_evidence_text=debate_evidence)
    ans = decisions_judge["answers"]

    resolution: AdversarialDebateResolution = structured_llm.invoke(
        [
            SystemMessage(content=DEBATE_ARBITER_PROMPT),
            HumanMessage(
                content=(
                    f"{debate_evidence}\n\n"
                    f"OpenAI Decisions API (`gpt-6-luna`) Debate Adjudication:\n"
                    f"{json.dumps(decisions_judge, indent=2)}\n\n"
                    "Resolve the debate and output `AdversarialDebateResolution`."
                )
            ),
        ]
    )
    winning_choice = ans.get("winning_firm_tilt", {}).get("choice")
    if winning_choice in ("NET_LONG_SELECTIVE", "MARKET_NEUTRAL_DISPERSION", "DEFENSIVE_HEDGED"):
        resolution.winning_tilt = winning_choice  # type: ignore[assignment]

    tilt_conf = ans.get("winning_firm_tilt", {}).get("confidence", 1.0)
    tail_prob = ans.get("tail_crash_probability", {}).get("probability", 0.5)
    bull_score = ans.get("bull_argument_rigor", {}).get("score", 1.0)
    bear_score = ans.get("bear_argument_rigor", {}).get("score", 1.0)

    msg = (
        f"### ⚖️ CIO Debate Arbiter Synthesis — Winning Tilt: `{resolution.winning_tilt}`\n"
        f"- **OpenAI Decisions API (`gpt-6-luna`) Telemetry**: Tilt Confidence `{tilt_conf:.0%}` | "
        f"Near-Term Tail Unwind Prob `{tail_prob:.0%}` | Bull Rigor Score `{bull_score:.2f}/2.0` vs Bear Rigor Score `{bear_score:.2f}/2.0`\n"
        f"- **Validated Bull Alpha**: {'; '.join(resolution.bull_valid_points)}\n"
        f"- **Validated Bear Risks & Hedges**: {'; '.join(resolution.bear_valid_risks)}\n"
        f"- **Desk Capital Weights**: {', '.join(resolution.desk_allocation_weights_pct)}\n"
        f"- **CIO Arbiter Memo**: {resolution.cio_synthesis_memo}"
    )
    resolution.openai_decisions_api = ans
    return {
        "adversarial_debate": {"resolution": resolution},
        "messages": [AIMessage(content=msg, name="CIO_Debate_Arbiter")],
    }


# ============================================================================
# TIER 3: 4 COMPILED MULTI-AGENT STRATEGY DESK SUBGRAPHS
# ============================================================================

def _format_desk_proposal_markdown(proposal: DeskMandateProposal, revision_count: int) -> str:
    rev_tag = f" (Revision #{revision_count})" if revision_count > 0 else " (Initial Subgraph Book)"
    legs_md = []
    for i, leg in enumerate(proposal.proposed_legs, 1):
        legs_md.append(
            f"  {i}. `{leg.direction}` **{leg.ticker}** ({leg.instrument} | *{leg.strategy_type}*) — "
            f"**`${leg.notional_usd:,.0f}`** @ Entry `${leg.entry_price:.2f}` | "
            f"Target `${leg.target_price:.2f}` | Stop `${leg.stop_loss:.2f}` ({leg.conviction})"
        )
    legs_block = "\n".join(legs_md)
    out = (
        f"### 🎯 {proposal.desk_name} Subgraph{rev_tag}\n"
        f"- **Gross Notional**: `${proposal.total_gross_notional_usd:,.0f}` | "
        f"**Net Exposure**: `${proposal.total_net_notional_usd:+,.0f}`\n"
        f"- **Desk Head Sign-Off**: {proposal.desk_head_summary}\n"
        f"- **Approved Subgraph Strategy Legs**:\n{legs_block}"
    )
    if proposal.revision_adjustments:
        out += f"\n- **CRO Revision Adjustments**: {proposal.revision_adjustments}"
    return out


def _build_desk_context(state: TradingDeskState, desk_name: str) -> str:
    """Prompt context for one desk, built by the parent so the desk subgraph needs no mirror of parent state."""
    intel = state.get("intel_reports") or {}
    dossiers = state.get("ticker_dossiers") or {}
    debate = state.get("adversarial_debate") or {}
    rev = state.get("revision_count") or 0
    prior_desk = (state.get("desk_proposals") or {}).get(desk_name)
    prior_risk = state.get("risk_assessment")

    parts = [
        f"TARGET STRATEGY DESK: {desk_name} (Revision Round: {rev})\n",
        f"SINGLE-NAME DEEP DOSSIERS (USE THESE EXACT LIVE SPOT PRICES):\n{_json(dossiers)}\n",
        f"ADVERSARIAL DEBATE RESOLUTION:\n{_json(debate.get('resolution'))}\n",
        f"TIER-1 INTELLIGENCE SUMMARY:\n{_json(intel)}\n",
    ]
    if rev > 0 and prior_risk:
        directive = next(
            (d.directive for d in prior_risk.desk_directives if d.desk_name == desk_name),
            "Maintain compliant book and tight stops.",
        )
        parts.append(
            f"=== URGENT CRO RISK COMMITTEE DIRECTIVE (REVISION ROUND #{rev}) ===\n"
            f"Your Previous {desk_name} Book:\n{_json(prior_desk)}\n\n"
            f"Firm-Wide Active Breaches:\n{_json(prior_risk.limit_breaches)}\n\n"
            f"Specific Directive for {desk_name}:\n{directive}\n\n"
            "MANDATORY COMPLIANCE RULES FOR REVISION:\n"
            "1. Every single leg `notional_usd` MUST be <= $9,000,000 (and total desk gross <= $18,000,000).\n"
            "2. Every single leg `stop_loss` MUST be within <= 6.2% of `entry_price`.\n"
            "3. For `MOMENTUM_DESK`, use `CALL_SPREAD` on NVDA/Semiconductors to cap tail VaR and keep firm Semiconductor concentration <= 28% of NAV.\n"
            "4. For `STAT_ARB_DESK`, ensure the SHORT Semiconductor leg (`SHORT SMH` or `SHORT AMD`) is at least as large as any LONG Semiconductor leg."
        )
    return "\n".join(parts)


def _desk_input(state: TradingDeskState, desk_name: str) -> DeskInput:
    return {
        "desk_name": desk_name,  # type: ignore[typeddict-item]
        "revision_count": state.get("revision_count") or 0,
        "context": _build_desk_context(state, desk_name),
    }


def dispatch_strategy_desks(state: TradingDeskState) -> list[Send]:
    """Debate arbiter -> one `Send` per desk into the single shared desk subgraph."""
    return [Send(DESK_NODE, _desk_input(state, desk)) for desk in ALL_DESKS]


def _enforce_revision_limits(proposal: DeskMandateProposal) -> None:
    """Hard clamps on revision rounds so a desk head cannot re-submit an oversized book."""
    for leg in proposal.proposed_legs:
        if leg.notional_usd > 8_500_000:
            leg.notional_usd = 8_000_000.0
        if leg.entry_price > 0:
            dist_pct = abs(leg.entry_price - leg.stop_loss) / leg.entry_price * 100.0
            if dist_pct > 6.5:
                leg.stop_loss = round(leg.entry_price * (0.94 if leg.direction == "LONG" else 1.06), 2)
        if proposal.desk_name == "MOMENTUM_DESK" and leg.ticker in ("NVDA", "AMD", "SMH") and leg.instrument == "EQUITY":
            leg.instrument = "CALL_SPREAD"


def desk_quant_modeler_node(state: DeskState) -> dict[str, Any]:
    """Desk subgraph, step 1: the Quant Modeler drafts candidate legs. The draft stays private to the subgraph."""
    desk_name = state["desk_name"]
    llm = _get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(DeskQuantModelOutput)

    quant_draft: DeskQuantModelOutput = structured_llm.invoke(
        [
            SystemMessage(content=DESK_QUANT_PROMPT.format(desk_name=desk_name)),
            HumanMessage(content=state["context"]),
        ]
    )
    quant_draft.desk_name = desk_name
    for leg in quant_draft.candidate_legs:
        leg.desk = desk_name
    return {"quant_draft": quant_draft}


def desk_head_pm_node(state: DeskState) -> dict[str, Any]:
    """Desk subgraph, step 2: the Desk Head signs off and the proposal is written back to the parent."""
    desk_name = state["desk_name"]
    rev = state["revision_count"]
    llm = _get_llm(temperature=0.1)
    structured_llm = llm.with_structured_output(DeskMandateProposal)

    proposal: DeskMandateProposal = structured_llm.invoke(
        [
            SystemMessage(content=DESK_HEAD_SUBGRAPH_PROMPT.format(desk_name=desk_name)),
            HumanMessage(
                content=(
                    f"{state['context']}\n\n"
                    f"Desk Quant Modeler Candidate Output:\n{_json(state['quant_draft'])}\n\n"
                    f"Review, refine, and issue the final signed-off `DeskMandateProposal` for `{desk_name}`."
                )
            ),
        ]
    )
    proposal.desk_name = desk_name
    for leg in proposal.proposed_legs:
        leg.desk = desk_name
    if rev > 0:
        _enforce_revision_limits(proposal)

    proposal.total_gross_notional_usd = sum(abs(l.notional_usd) for l in proposal.proposed_legs)
    proposal.total_net_notional_usd = sum(
        l.notional_usd if l.direction == "LONG" else -l.notional_usd
        for l in proposal.proposed_legs
    )

    return {
        "desk_proposals": {desk_name: proposal},
        "messages": [AIMessage(content=_format_desk_proposal_markdown(proposal, rev), name=f"{desk_name}_Subgraph")],
    }


def build_strategy_desk_subgraph():
    """One compiled 2-node subgraph (`desk_quant_modeler -> desk_head_pm`) shared by all four desks.

    The desk is chosen by `desk_name` in the `Send()` payload, so a revision round re-runs only the flagged desks.
    """
    sub = StateGraph(DeskState, input_schema=DeskInput, output_schema=DeskOutput)
    sub.add_node("desk_quant_modeler", desk_quant_modeler_node)
    sub.add_node("desk_head_pm", desk_head_pm_node)
    sub.add_edge(START, "desk_quant_modeler")
    sub.add_edge("desk_quant_modeler", "desk_head_pm")
    sub.add_edge("desk_head_pm", END)
    return sub.compile()


strategy_desk_subgraph = build_strategy_desk_subgraph()


# ============================================================================
# TIER 4: TRIAD RISK & EXPOSURE COMMITTEE (PARALLEL AUDITORS -> CRO SYNTHESIS)
# ============================================================================

def _collect_all_proposed_legs(state: TradingDeskState) -> list[dict[str, Any]]:
    """Flatten every desk's legs into the plain dicts the deterministic risk-engine tool takes."""
    desks = state.get("desk_proposals") or {}
    return [
        leg.model_dump()
        for d_name in ALL_DESKS
        if d_name in desks
        for leg in desks[d_name].proposed_legs
    ]


def factor_exposure_auditor_node(state: TradingDeskState) -> dict[str, Any]:
    """Factor & Sector Concentration Auditor node (runs in parallel with Tail-VaR Stress Tester)."""
    llm = _get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(FactorExposureAudit)

    all_legs = _collect_all_proposed_legs(state)
    quant_eval = evaluate_multi_desk_book_risk.invoke({"proposed_legs": all_legs})

    audit: FactorExposureAudit = structured_llm.invoke(
        [
            SystemMessage(content=FACTOR_EXPOSURE_AUDITOR_PROMPT),
            HumanMessage(
                content=(
                    f"Quantitative Multi-Desk Risk Evaluation:\n{json.dumps(quant_eval, indent=2)}\n\n"
                    "Produce the `FactorExposureAudit`."
                )
            ),
        ]
    )
    audit.post_trade_gross_pct_nav = quant_eval["post_trade_firm_gross_pct_nav"]
    audit.post_trade_net_pct_nav = quant_eval["post_trade_firm_net_pct_nav"]
    audit.net_beta_spy_equivalent_usd = quant_eval["net_beta_adjusted_exposure_usd"]

    return {"risk_sub_reports": {"factor_audit": audit}}


def tail_var_monte_carlo_node(state: TradingDeskState) -> dict[str, Any]:
    """Tail-Risk, VaR & Monte Carlo Stress Tester node (runs in parallel with Factor Auditor)."""
    llm = _get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(TailRiskMonteCarloReport)

    all_legs = _collect_all_proposed_legs(state)
    quant_eval = evaluate_multi_desk_book_risk.invoke({"proposed_legs": all_legs})

    mc_report: TailRiskMonteCarloReport = structured_llm.invoke(
        [
            SystemMessage(content=TAIL_VAR_MONTE_CARLO_PROMPT),
            HumanMessage(
                content=(
                    f"Quantitative Multi-Desk Risk Evaluation:\n{json.dumps(quant_eval, indent=2)}\n\n"
                    "Produce the `TailRiskMonteCarloReport`."
                )
            ),
        ]
    )
    mc_report.parametric_var_99_1d_usd = quant_eval["portfolio_var_99_1d_usd"]
    mc_report.expected_shortfall_cvar_99_usd = quant_eval["expected_shortfall_cvar_99_usd"]
    mc_report.stressed_regime_var_usd = quant_eval["stressed_var_99_usd"]

    return {"risk_sub_reports": {"tail_var_mc": mc_report}}


def _engine_verdict(quant_eval: dict[str, Any], rev: int, max_rev: int) -> tuple[str, list[str]]:
    """The CRO gate, decided by the deterministic engine alone. Fails closed once revisions are exhausted."""
    if quant_eval["all_checks_passed"]:
        return "APPROVED", []
    return ("REVISE_DESKS" if rev < max_rev else "REJECTED"), list(quant_eval["limit_breaches"])


def _engine_commentary(
    quant_eval: dict[str, Any],
    status: str,
    rev: int,
    max_rev: int,
    directives: list[DeskRiskDirective],
) -> str:
    """Commentary built only from the engine's numbers, used when the LLM's narrative disagrees with the verdict."""
    parts = [
        f"Risk engine verdict: {status} (revision round {rev} of max {max_rev}).",
        f"Post-trade exposure is {quant_eval['post_trade_firm_gross_pct_nav']:.1f}% gross / "
        f"{quant_eval['post_trade_firm_net_pct_nav']:+.1f}% net of NAV; diversified 99% 1-day VaR is "
        f"${quant_eval['portfolio_var_99_1d_usd']:,.0f} against a ${quant_eval['max_portfolio_var_99_limit_usd']:,.0f} limit; "
        f"stressed VaR is ${quant_eval['stressed_var_99_usd']:,.0f}.",
    ]
    breaches = quant_eval["limit_breaches"]
    if breaches:
        parts.append(f"{len(breaches)} limit breach(es): " + " ".join(breaches))
        flagged = [d.desk_name for d in directives if d.must_revise]
        if flagged:
            parts.append("Desks flagged for revision: " + ", ".join(flagged) + ".")
    else:
        parts.append("No limit breaches.")
    return " ".join(parts)


def cro_risk_synthesis_node(state: TradingDeskState) -> dict[str, Any]:
    """Chief Risk Officer (CRO) Synthesis node: combines Factor Audit + Tail-VaR Stress Report + OpenAI Decisions API Gate."""
    llm = _get_llm()
    structured_llm = llm.with_structured_output(PortfolioRiskReport)

    sub_reports = state.get("risk_sub_reports") or {}
    all_legs = _collect_all_proposed_legs(state)
    quant_eval = evaluate_multi_desk_book_risk.invoke({"proposed_legs": all_legs})
    firm_mandate = get_firm_portfolio_and_mandate.invoke({})

    rev = state.get("revision_count") or 0
    max_rev = state.get("max_revisions") or 2
    engine_status, engine_breaches = _engine_verdict(quant_eval, rev, max_rev)
    flagged_desks = [k for k, v in quant_eval.get("desk_must_revise_map", {}).items() if v]

    risk_evidence_str = (
        f"Revision Round: {rev} of max {max_rev}\n"
        f"Firm Mandate & Live Alpaca Paper Account:\n{json.dumps(firm_mandate, indent=2)}\n\n"
        f"Factor Exposure Audit:\n{_json(sub_reports.get('factor_audit'))}\n\n"
        f"Tail-VaR & Monte Carlo Stress Report:\n{_json(sub_reports.get('tail_var_mc'))}\n\n"
        f"Deterministic Quantitative Risk Engine:\n{json.dumps(quant_eval, indent=2)}"
    )

    # Call OpenAI Decisions API (client.decisions.create with gpt-6-luna) for multi-desk risk gating & fragility scoring
    cro_decisions = evaluate_cro_gate_via_decisions_api(risk_evidence_text=risk_evidence_str)
    dec_ans = cro_decisions["answers"]

    report: PortfolioRiskReport = structured_llm.invoke(
        [
            SystemMessage(content=CRO_SYNTHESIS_PROMPT),
            HumanMessage(
                content=(
                    f"{risk_evidence_str}\n\n"
                    f"OpenAI Decisions API (`gpt-6-luna`) CRO Risk Gate:\n"
                    f"{json.dumps(cro_decisions, indent=2)}\n\n"
                    f"DETERMINISTIC RISK ENGINE VERDICT (authoritative and final): `{engine_status}`.\n"
                    f"Limit breaches: {json.dumps(engine_breaches)}\n"
                    f"Desks flagged for revision: {json.dumps(flagged_desks)}\n"
                    "Set `status` to this verdict and write `cro_commentary` consistent with it. "
                    "Do not cite breaches that the engine did not report.\n\n"
                    "Produce the master `PortfolioRiskReport`."
                )
            ),
        ]
    )
    llm_status = report.status

    must_revise_map = quant_eval.get("desk_must_revise_map", {})
    quant_directives = [
        DeskRiskDirective(
            desk_name=k,  # type: ignore[arg-type]
            must_revise=bool(must_revise_map.get(k, False)),
            directive=v,
        )
        for k, v in quant_eval["desk_specific_directives"].items()
    ]

    # The deterministic engine decides the gate; the LLM's own `status` is never trusted.
    # Fail closed: breaches that survive the last revision round are REJECTED, not waved through.
    report.status = engine_status  # type: ignore[assignment]
    report.limit_breaches = engine_breaches
    report.desk_directives = quant_directives
    if llm_status != engine_status:
        # The LLM wrote its narrative for a different verdict, so it would contradict the gate. Use the engine's.
        report.cro_commentary = (
            f"{_engine_commentary(quant_eval, engine_status, rev, max_rev, quant_directives)} "
            f"(The LLM CRO draft proposed {llm_status}; its narrative was replaced because it disagreed "
            "with the deterministic engine.)"
        )

    report.combined_gross_exposure_pct_nav = quant_eval["post_trade_firm_gross_pct_nav"]
    report.combined_net_exposure_pct_nav = quant_eval["post_trade_firm_net_pct_nav"]
    report.portfolio_var_99_1d_usd = quant_eval["portfolio_var_99_1d_usd"]
    report.stressed_var_99_usd = quant_eval["stressed_var_99_usd"]
    report.net_beta_adjusted_exposure_usd = quant_eval["net_beta_adjusted_exposure_usd"]

    badge = {
        "APPROVED": "✅ APPROVED (ALL TRIAD RISK MANDATES SATISFIED)",
        "REVISE_DESKS": "⚠️ SELECTIVE DESK REVISION REQUIRED",
        "REJECTED": "🛑 REJECTED",
    }.get(report.status, report.status)

    breaches_md = (
        "\n".join(f"  - ❌ {b}" for b in report.limit_breaches)
        if report.limit_breaches
        else "  - ✅ Zero breaches across single-leg caps, desk gross caps, 28% sector concentration, stop distance, and $1.45M VaR"
    )
    directives_md = "\n".join(
        f"  - **{d.desk_name}** (`must_revise={d.must_revise}`): {d.directive}"
        for d in report.desk_directives
    )
    shocks = quant_eval.get("scenario_shocks_usd", {})
    frag_score = dec_ans.get("firm_tail_fragility_score", {}).get("score", 0.0)
    mom_prob = dec_ans.get("momentum_desk_needs_revision", {}).get("probability", 0.0)
    stat_prob = dec_ans.get("stat_arb_desk_needs_revision", {}).get("probability", 0.0)
    vol_prob = dec_ans.get("macro_vol_desk_needs_revision", {}).get("probability", 0.0)
    evt_prob = dec_ans.get("event_arb_desk_needs_revision", {}).get("probability", 0.0)

    msg = (
        f"### 🛡️ Triad Risk Committee & CRO Synthesis — {badge}\n"
        f"- **Risk Score**: `{report.risk_score}/10` | **Post-Trade Exposure**: `{report.combined_gross_exposure_pct_nav:.1f}%` Gross / `{report.combined_net_exposure_pct_nav:+.1f}%` Net of $100M NAV\n"
        f"- **Diversified 99% 1-Day VaR**: `${report.portfolio_var_99_1d_usd:,.0f}` | **99% CVaR**: `${quant_eval['expected_shortfall_cvar_99_usd']:,.0f}` | **3.5σ Stressed VaR**: `${report.stressed_var_99_usd:,.0f}`\n"
        f"- **OpenAI Decisions API (`gpt-6-luna`) Risk Gate**: Tail Fragility `{frag_score:.2f}/3.0` | "
        f"Desk Revision Probs: `MOM={mom_prob:.0%}`, `STAT={stat_prob:.0%}`, `VOL={vol_prob:.0%}`, `EVT={evt_prob:.0%}`\n"
        f"- **Stress Scenarios**: AI/Semis -12% Unwind `${shocks.get('ai_semis_unwind_minus_12pct_usd', 0):+,.0f}` | Rates +50bps `${shocks.get('rates_shock_plus_50bps_usd', 0):+,.0f}`\n"
        f"- **Limit Breaches**:\n{breaches_md}\n"
        f"- **Targeted Desk Directives**:\n{directives_md}\n"
        f"- **CRO Commentary**: {report.cro_commentary}"
    )

    next_rev = rev + 1 if report.status == "REVISE_DESKS" else rev
    report.openai_decisions_api = dec_ans
    return {
        "risk_assessment": report,
        "revision_count": next_rev,
        "messages": [AIMessage(content=msg, name="CRO_Risk_Synthesis")],
    }


def route_after_cro_synthesis(state: TradingDeskState) -> list[Send] | str:
    """Gate after the CRO. Only an explicit APPROVED reaches the Head of Trading; anything else fails closed.

    REVISE_DESKS re-dispatches ONLY the desks flagged with `must_revise=True`.
    """
    risk = state.get("risk_assessment")
    rev = state.get("revision_count") or 0
    max_rev = state.get("max_revisions") or 2

    if risk is None:
        return "halt_no_trade"
    if risk.status == "APPROVED":
        return "head_of_trading"
    if risk.status == "REVISE_DESKS" and rev <= max_rev:
        flagged = [d.desk_name for d in risk.desk_directives if d.must_revise] or ALL_DESKS
        return [Send(DESK_NODE, _desk_input(state, desk)) for desk in flagged]
    return "halt_no_trade"


def halt_no_trade_node(state: TradingDeskState) -> dict[str, Any]:
    """Terminal node for a CRO REJECTED verdict: nothing is released to the Head of Trading or the EMS router."""
    risk = state.get("risk_assessment")
    breaches = risk.limit_breaches if risk else ["No CRO risk assessment was produced."]
    breaches_md = "\n".join(f"  - ❌ {b}" for b in breaches)
    msg = (
        f"### 🛑 Trading Halted — CRO Risk Gate `{risk.status if risk else 'MISSING'}`\n"
        f"- **Revision Rounds Used**: `{state.get('revision_count') or 0}` of max `{state.get('max_revisions') or 2}`\n"
        f"- **Unresolved Breaches**:\n{breaches_md}\n"
        "- **Outcome**: No parent tickets were released to the Head of Trading and no orders were staged."
    )
    return {"messages": [AIMessage(content=msg, name="CRO_Trading_Halt")]}


# ============================================================================
# TIER 5: GLOBAL HEAD OF TRADING -> EXECUTION MICROSTRUCTURE EMS ROUTER
# ============================================================================

def head_of_trading_node(state: TradingDeskState) -> dict[str, Any]:
    """Global Head of Trading node: issues master capital allocation and parent execution tickets."""
    llm = _get_llm()
    structured_llm = llm.with_structured_output(HeadOfTradingDecision)

    decision: HeadOfTradingDecision = structured_llm.invoke(
        [
            SystemMessage(content=HEAD_OF_TRADING_PROMPT),
            HumanMessage(
                content=(
                    f"Completed Revision Rounds: {state.get('revision_count')}\n\n"
                    f"Adversarial Debate Resolution:\n{_json((state.get('adversarial_debate') or {}).get('resolution'))}\n\n"
                    f"4 Strategy Desk Subgraph Proposals:\n{_json(state.get('desk_proposals'))}\n\n"
                    f"CRO Risk Committee Report:\n{_json(state.get('risk_assessment'))}\n\n"
                    "Produce the master `HeadOfTradingDecision`."
                )
            ),
        ]
    )

    # Routing only sends APPROVED books here (see `route_after_cro_synthesis`).
    if not decision.execution_blotter:
        # Ensure all CRO-approved desk legs are staged as parent tickets for the EMS router
        desks = state.get("desk_proposals") or {}
        tickets: list[ApprovedExecutionTicket] = []
        allocs: list[DeskCapitalAllocation] = []
        for d_name in ALL_DESKS:
            book = desks.get(d_name)
            legs = book.proposed_legs if book else []
            d_gross = 0.0
            d_net = 0.0
            for leg in legs:
                d_gross += abs(leg.notional_usd)
                d_net += leg.notional_usd if leg.direction == "LONG" else -leg.notional_usd
                tickets.append(
                    ApprovedExecutionTicket(
                        desk=d_name,
                        ticker=leg.ticker,
                        direction=leg.direction,
                        instrument=leg.instrument,
                        approved_notional_usd=leg.notional_usd,
                        limit_or_entry_price=leg.entry_price,
                        target_price=leg.target_price,
                        stop_loss=leg.stop_loss,
                        algo_routing="EMS_PENDING_DECISIONS_API",
                    )
                )
            allocs.append(
                DeskCapitalAllocation(
                    desk_name=d_name,  # type: ignore[arg-type]
                    approved_gross_usd=d_gross,
                    approved_net_usd=d_net,
                )
            )
        decision.overall_decision = "EXECUTE_SCALED_AND_HEDGED"
        decision.execution_blotter = tickets
        decision.desk_allocations = allocs
        decision.total_deployed_gross_usd = sum(a.approved_gross_usd for a in allocs)
        decision.total_deployed_net_usd = sum(a.approved_net_usd for a in allocs)

    alloc_str = " | ".join(
        f"**{a.desk_name}**: `${a.approved_gross_usd:,.0f}` Gross (`${a.approved_net_usd:+,.0f}` Net)"
        for a in decision.desk_allocations
    )
    blotter_rows = [
        f"  - `[{t.desk}]` **{t.direction} {t.ticker}** ({t.instrument}) — "
        f"**`${t.approved_notional_usd:,.0f}`** @ `${t.limit_or_entry_price:.2f}` "
        f"(Target `${t.target_price:.2f}` / Stop `${t.stop_loss:.2f}`) | Algo: `{t.algo_routing}`"
        for t in decision.execution_blotter
    ]

    msg = (
        f"### 🏛️ Global Head of Trading Master Blotter — `{decision.overall_decision}`\n"
        f"- **Total Deployed Capital**: `${decision.total_deployed_gross_usd:,.0f}` Gross | "
        f"`${decision.total_deployed_net_usd:+,.0f}` Net\n"
        f"- **Desk Allocations**: {alloc_str}\n"
        f"- **Approved Parent Execution Tickets ({len(decision.execution_blotter)})**:\n"
        + "\n".join(blotter_rows)
        + f"\n- **Executive Summary**: {decision.executive_summary}"
    )

    return {
        "head_of_trading_decision": decision,
        "messages": [AIMessage(content=msg, name="Head_Of_Trading")],
    }


def execution_microstructure_router_node(state: TradingDeskState) -> dict[str, Any]:
    """Quantitative Execution & Microstructure EMS Router node: OpenAI Decisions API algo selection + child order slicing."""
    llm = _get_llm()
    structured_llm = llm.with_structured_output(ExecutionMicrostructurePlan)

    hot_decision = state.get("head_of_trading_decision")
    premarket_intel = (state.get("intel_reports") or {}).get("premarket")
    dossiers = state.get("ticker_dossiers") or {}
    blotter = hot_decision.execution_blotter if hot_decision else []

    blotter_tickers = [t.ticker for t in blotter] or _extract_focus_tickers(state)

    ems_evidence = (
        f"Head of Trading Parent Execution Blotter:\n{_json(hot_decision)}\n\n"
        f"Databento L1 Orderbook Microstructure & Pre-Market Telemetry:\n{_json(premarket_intel)}\n\n"
        f"Ticker Dossiers (OBI & ATR):\n{_json(dossiers)}"
    )

    # Call OpenAI Decisions API (client.decisions.create with gpt-6-luna) for batched EMS algorithm & toxicity classification
    ems_decisions = select_ems_algos_via_decisions_api(
        ems_evidence_text=ems_evidence,
        tickers=blotter_tickers,
    )
    ems_ans = ems_decisions["answers"]

    plan: ExecutionMicrostructurePlan = structured_llm.invoke(
        [
            SystemMessage(content=EMS_MICROSTRUCTURE_ROUTER_PROMPT),
            HumanMessage(
                content=(
                    f"{ems_evidence}\n\n"
                    f"OpenAI Decisions API (`gpt-6-luna`) EMS Algo & Toxicity Selection:\n"
                    f"{json.dumps(ems_decisions, indent=2)}\n\n"
                    "Generate the `ExecutionMicrostructurePlan` with child-order slicing, TCA slippage estimates, and Alpaca v2/orders JSON payload previews."
                )
            ),
        ]
    )

    if not plan.child_order_schedules and blotter:
        # Populate deterministic child order schedules using the OpenAI Decisions API EMS choices
        schedules = []
        total_tca = 0.0
        for t in blotter:
            sym = t.ticker.upper().strip()
            notional = t.approved_notional_usd
            px = max(t.limit_or_entry_price, 1.0)
            shs = max(int(round(notional / px)), 100)
            dec_algo = ems_ans.get(f"{sym}_ems_algo", {}).get("choice") or "DARK_MIDPOINT_PEG_IEX_DLIMIT"
            tox_prob = float(ems_ans.get(f"{sym}_adverse_selection_toxicity", {}).get("probability", 0.5))
            slip_bps = round(6.0 + 10.0 * tox_prob, 1)
            total_tca += notional * (slip_bps / 10_000.0)
            side = "buy" if t.direction == "LONG" else "sell"
            schedules.append(
                ChildOrderSliceSchedule(
                    parent_desk=t.desk,
                    ticker=sym,
                    direction=t.direction,
                    instrument=t.instrument,
                    total_notional_usd=notional,
                    estimated_shares_or_contracts=shs,
                    selected_ems_algo=dec_algo,
                    venue_routing_split=(
                        "50% Dark Pools (SigmaX/Crossfinder), 35% IEX D-Limit, 15% Lit ARCA/NASDAQ"
                        if tox_prob >= 0.5
                        else "35% Dark Pools, 35% IEX Speedbump, 30% Lit Venues"
                    ),
                    num_child_slices=6,
                    expected_slippage_bps=slip_bps,
                    alpaca_order_payload_preview=json.dumps(
                        {
                            "symbol": sym,
                            "qty": shs,
                            "side": side,
                            "type": "limit",
                            "limit_price": round(px, 2),
                            "time_in_force": "day",
                        }
                    ),
                )
            )
        plan.child_order_schedules = schedules
        plan.total_parent_orders = len(schedules)
        plan.total_expected_tca_cost_usd = round(total_tca, 2)

    slice_rows = []
    for s in plan.child_order_schedules:
        sym = s.ticker.upper().strip()
        dec_algo = ems_ans.get(f"{sym}_ems_algo", {}).get("choice")
        dec_conf = ems_ans.get(f"{sym}_ems_algo", {}).get("confidence", 1.0)
        tox_prob = ems_ans.get(f"{sym}_adverse_selection_toxicity", {}).get("probability", 0.0)
        if dec_algo:
            s.selected_ems_algo = dec_algo
        slice_rows.append(
            f"  - `[{s.parent_desk}]` **{s.direction} {s.ticker}** (`${s.total_notional_usd:,.0f}` | `{s.estimated_shares_or_contracts:,}` shs/contracts)\n"
            f"    - **EMS Algo (`gpt-6-luna` Decisions API conf `{dec_conf:.0%}` | Toxicity Prob `{tox_prob:.0%}`)**: `{s.selected_ems_algo}` ({s.num_child_slices} child slices | Est. Slippage: `{s.expected_slippage_bps:.1f} bps`)\n"
            f"    - **Venue Split**: {s.venue_routing_split}\n"
            f"    - **Alpaca Order Payload**: `{s.alpaca_order_payload_preview}`"
        )

    msg = (
        f"### ⚡ Quantitative Execution & Microstructure EMS Router (TCA & Child Slicing + Decisions API)\n"
        f"- **Parent Orders Staged**: `{plan.total_parent_orders}` | **Estimated Total TCA Cost**: `${plan.total_expected_tca_cost_usd:,.0f}`\n"
        f"- **Child-Order Slicing & Venue Routing Schedule**:\n"
        + "\n".join(slice_rows)
        + f"\n- **EMS Execution Desk Commentary**: {plan.ems_execution_commentary}"
    )

    plan.openai_decisions_api = ems_ans
    return {
        "execution_microstructure_plan": plan,
        "messages": [AIMessage(content=msg, name="Execution_Microstructure_EMS_Router")],
    }


# ============================================================================
# MASTER 24-NODE HIERARCHICAL GRAPH BUILDER
# ============================================================================

def build_trading_desk_graph(checkpointer=None):
    """Compile the 24-Node Hierarchical Multi-Strategy Hedge Fund StateGraph."""
    builder = StateGraph(TradingDeskState)

    # Tier 0: CIO Regime Router
    builder.add_node("cio_regime_router", cio_regime_router_node)

    # Tier 1: 6-Way Parallel Intelligence Syndicate + Dynamic Send() Ticker Worker + Aggregator
    builder.add_node("premarket_microstructure", premarket_microstructure_node)
    builder.add_node("technical_analyst", technical_analyst_node)
    builder.add_node("macro_strategist", macro_strategist_node)
    builder.add_node("news_sentiment", news_sentiment_node)
    builder.add_node("vol_surface_analyst", vol_surface_analyst_node)
    builder.add_node("factor_cointegration_analyst", factor_cointegration_analyst_node)
    builder.add_node("ticker_deep_research_worker", ticker_deep_research_worker)
    builder.add_node("intelligence_aggregator", intelligence_aggregator_node)

    # Tier 2: Adversarial Bull / Bear / CIO Debate Chamber
    builder.add_node("bull_thesis_advocate", bull_thesis_advocate_node)
    builder.add_node("bear_thesis_critic", bear_thesis_critic_node)
    builder.add_node("adversarial_debate_arbiter", adversarial_debate_arbiter_node)

    # Tier 3: one 2-node Strategy Desk subgraph, run once per desk via Send() (4 parallel runs per round)
    builder.add_node(DESK_NODE, strategy_desk_subgraph)

    # Tier 4: Triad Risk & Exposure Committee (2 Parallel Risk Auditors -> CRO Synthesis)
    builder.add_node("factor_exposure_auditor", factor_exposure_auditor_node)
    builder.add_node("tail_var_monte_carlo", tail_var_monte_carlo_node)
    builder.add_node("cro_risk_synthesis", cro_risk_synthesis_node)
    builder.add_node("halt_no_trade", halt_no_trade_node)

    # Tier 5: Global Head of Trading -> Quantitative Execution & Microstructure EMS Router
    builder.add_node("head_of_trading", head_of_trading_node)
    builder.add_node("execution_microstructure_router", execution_microstructure_router_node)

    # --- WIRING THE HIERARCHICAL TOPOLOGY ---

    # 1. Start -> CIO Regime Router -> Dynamic Send() + 6 Parallel Intelligence Analysts
    builder.add_edge(START, "cio_regime_router")
    builder.add_conditional_edges(
        "cio_regime_router",
        fan_out_from_cio_router,
        [
            "premarket_microstructure",
            "technical_analyst",
            "macro_strategist",
            "news_sentiment",
            "vol_surface_analyst",
            "factor_cointegration_analyst",
            "ticker_deep_research_worker",
        ],
    )

    # 2. All Tier-1 Intelligence nodes + dynamic ticker workers converge at Intelligence Aggregator
    builder.add_edge("premarket_microstructure", "intelligence_aggregator")
    builder.add_edge("technical_analyst", "intelligence_aggregator")
    builder.add_edge("macro_strategist", "intelligence_aggregator")
    builder.add_edge("news_sentiment", "intelligence_aggregator")
    builder.add_edge("vol_surface_analyst", "intelligence_aggregator")
    builder.add_edge("factor_cointegration_analyst", "intelligence_aggregator")
    builder.add_edge("ticker_deep_research_worker", "intelligence_aggregator")

    # 3. Intelligence Aggregator fans out to Adversarial Bull & Bear Directors in parallel
    builder.add_edge("intelligence_aggregator", "bull_thesis_advocate")
    builder.add_edge("intelligence_aggregator", "bear_thesis_critic")
    builder.add_edge("bull_thesis_advocate", "adversarial_debate_arbiter")
    builder.add_edge("bear_thesis_critic", "adversarial_debate_arbiter")

    # 4. Debate Arbiter dispatches one Send() per desk into the shared Strategy Desk subgraph
    builder.add_conditional_edges("adversarial_debate_arbiter", dispatch_strategy_desks, [DESK_NODE])

    # 5. The desk runs feed into the 2 Parallel Risk Auditors
    builder.add_edge(DESK_NODE, "factor_exposure_auditor")
    builder.add_edge(DESK_NODE, "tail_var_monte_carlo")

    # 6. Parallel Risk Auditors converge at CRO Risk Synthesis
    builder.add_edge("factor_exposure_auditor", "cro_risk_synthesis")
    builder.add_edge("tail_var_monte_carlo", "cro_risk_synthesis")

    # 7. CRO gate: selective desk re-dispatch, approval to the Head of Trading, or a fail-closed halt
    builder.add_conditional_edges(
        "cro_risk_synthesis",
        route_after_cro_synthesis,
        [DESK_NODE, "head_of_trading", "halt_no_trade"],
    )
    builder.add_edge("halt_no_trade", END)

    # 8. Head of Trading -> Execution Microstructure EMS Router -> END
    builder.add_edge("head_of_trading", "execution_microstructure_router")
    builder.add_edge("execution_microstructure_router", END)

    return builder.compile(checkpointer=checkpointer)


graph = build_trading_desk_graph()
