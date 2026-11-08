"""OpenAI Decisions API (`client.decisions.create` with `gpt-6-luna`) integration with LangSmith tracing."""

from __future__ import annotations

import os
from typing import Any

import openai
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv(override=True)

DECISIONS_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")


def _get_openai_client() -> openai.OpenAI:
    return openai.OpenAI()


def _parse_decision_answers(decision_resp: Any) -> dict[str, dict[str, Any]]:
    """Convert a `Decision` response into a dictionary keyed by question `name` with full probability distributions."""
    parsed: dict[str, dict[str, Any]] = {}
    for idx, ans in enumerate(getattr(decision_resp, "answers", [])):
        key = getattr(ans, "name", None) or f"q_{idx}"
        ans_type = getattr(ans, "type", "unknown")
        if ans_type == "predicate":
            parsed[key] = {
                "type": "predicate",
                "probability": round(float(ans.probability), 4),
            }
        elif ans_type == "choice":
            parsed[key] = {
                "type": "choice",
                "choice": ans.choice,
                "confidence": round(float(ans.confidence), 4),
                "probabilities": {
                    str(p.value): round(float(p.probability), 4)
                    for p in getattr(ans, "probabilities", [])
                },
            }
        elif ans_type == "score":
            parsed[key] = {
                "type": "score",
                "score": round(float(ans.score), 4),
                "confidence": round(float(ans.confidence), 4),
                "distribution": [
                    {
                        "label": p.label,
                        "value": p.value,
                        "probability": round(float(p.probability), 4),
                    }
                    for p in getattr(ans, "probabilities", [])
                ],
            }
        else:
            parsed[key] = {"type": ans_type}
    return parsed


@traceable(run_type="llm", name="OpenAI_Decisions_API_Ticker_Gate")
def evaluate_ticker_via_decisions_api(
    ticker: str,
    evidence_text: str,
) -> dict[str, Any]:
    """Use `client.decisions.create(model='gpt-6-luna')` to evaluate a single ticker's setup with calibrated probabilities."""
    client = _get_openai_client()
    questions = [
        {
            "name": "institutional_orderflow_supportive",
            "type": "predicate",
            "instructions": (
                f"Does the Databento L1 orderbook microstructure and Alpaca tape for {ticker} "
                "show supportive liquidity or a tradable institutional dislocation?"
            ),
        },
        {
            "name": "event_or_tail_risk_elevated",
            "type": "predicate",
            "instructions": (
                f"Does {ticker} face elevated earnings, regulatory, geopolitical, or macro tail risk "
                "that warrants defined-risk options structures (such as spreads or collars)?"
            ),
        },
        {
            "name": "preferred_direction",
            "type": "choice",
            "instructions": f"Select the optimal directional bias for {ticker} based on all technical, news, and microstructure evidence.",
            "choices": [
                {"value": "LONG", "description": "Bullish breakout or oversold mean-reversion long"},
                {"value": "SHORT", "description": "Bearish breakdown or overbought mean-reversion short"},
                {"value": "NEUTRAL_PAIR", "description": "Market-neutral statistical arbitrage pair leg"},
            ],
        },
        {
            "name": "optimal_instrument",
            "type": "choice",
            "instructions": f"Select the best execution instrument structure for {ticker}.",
            "choices": [
                {"value": "EQUITY", "description": "Outright delta-1 cash equity"},
                {"value": "CALL_SPREAD", "description": "Bullish or defined-risk vertical call spread"},
                {"value": "PUT_SPREAD", "description": "Bearish or tail-hedge vertical put spread"},
                {"value": "COLLAR", "description": "Zero-cost options collar"},
                {"value": "PAIR_LEG", "description": "Cointegrated relative-value pair leg"},
            ],
        },
        {
            "name": "setup_conviction_tier",
            "type": "score",
            "instructions": f"Rate the overall risk-adjusted alpha quality of the {ticker} setup.",
            "levels": [
                {"label": "low_conviction", "description": "Noisy or conflicting signals"},
                {"label": "medium_conviction", "description": "Solid statistical or macro edge"},
                {"label": "high_conviction", "description": "Exceptional multi-factor alignment"},
                {"label": "marquee_institutional_conviction", "description": "Top-decile asymmetric institutional trade"},
            ],
        },
    ]

    decision = client.decisions.create(
        model=DECISIONS_MODEL,
        input=evidence_text,
        questions=questions,  # type: ignore[arg-type]
    )
    return {
        "ticker": ticker,
        "model": decision.model,
        "answers": _parse_decision_answers(decision),
        "usage": decision.usage.model_dump() if getattr(decision, "usage", None) else {},
    }


@traceable(run_type="llm", name="OpenAI_Decisions_API_Debate_Judge")
def judge_debate_via_decisions_api(
    debate_evidence_text: str,
) -> dict[str, Any]:
    """Use `client.decisions.create(model='gpt-6-luna')` to probabilistically score Bull vs Bear arguments and select the winning firm tilt."""
    client = _get_openai_client()
    questions = [
        {
            "name": "winning_firm_tilt",
            "type": "choice",
            "instructions": "After weighing the Bull Director vs Bear Short-Seller arguments against the live market data, select the optimal firm-wide portfolio tilt.",
            "choices": [
                {"value": "NET_LONG_SELECTIVE", "description": "Pro-cyclical net long tilt focused on oversold leaders"},
                {"value": "MARKET_NEUTRAL_DISPERSION", "description": "Balanced long/short dispersion and relative-value pairs"},
                {"value": "DEFENSIVE_HEDGED", "description": "Defensive posture with heavy index put-spread and duration hedges"},
            ],
        },
        {
            "name": "tail_crash_probability",
            "type": "predicate",
            "instructions": "Is there a meaningful probability of near-term equity factor unwind or macro/regulatory drawdown requiring active index put spreads?",
        },
        {
            "name": "bull_argument_rigor",
            "type": "score",
            "instructions": "Rate the empirical and quantitative rigor of the Bull Research Director's thesis.",
            "levels": [
                {"label": "weak", "description": "Ignores key risks or data"},
                {"label": "moderate", "description": "Supported by selective technical/macro indicators"},
                {"label": "strong", "description": "Compelling multi-factor quantitative evidence"},
            ],
        },
        {
            "name": "bear_argument_rigor",
            "type": "score",
            "instructions": "Rate the empirical and quantitative rigor of the Forensic Bear Short-Seller's thesis.",
            "levels": [
                {"label": "weak", "description": "Overly pessimistic without data support"},
                {"label": "moderate", "description": "Highlights valid crowding or news risks"},
                {"label": "strong", "description": "Compelling forensic, sentiment, and microstructure evidence"},
            ],
        },
    ]

    decision = client.decisions.create(
        model=DECISIONS_MODEL,
        input=debate_evidence_text,
        questions=questions,  # type: ignore[arg-type]
    )
    return {
        "model": decision.model,
        "answers": _parse_decision_answers(decision),
        "usage": decision.usage.model_dump() if getattr(decision, "usage", None) else {},
    }


@traceable(run_type="llm", name="OpenAI_Decisions_API_CRO_Risk_Gate")
def evaluate_cro_gate_via_decisions_api(
    risk_evidence_text: str,
) -> dict[str, Any]:
    """Use `client.decisions.create(model='gpt-6-luna')` to run a multi-question probabilistic risk gate across all 4 strategy desks."""
    client = _get_openai_client()
    questions = [
        {
            "name": "cro_gate_action",
            "type": "choice",
            "instructions": "Select the required CRO Risk Committee action based on whether `all_checks_passed` is True or if any limit breaches exist.",
            "choices": [
                {"value": "APPROVED", "description": "Zero quantitative limit breaches across all desks and firm VaR"},
                {"value": "REVISE_DESKS", "description": "One or more single-leg, sector concentration, stop-distance, or VaR limits breached"},
                {"value": "REJECTED", "description": "Severe unresolvable breaches after maximum revisions"},
            ],
        },
        {
            "name": "momentum_desk_needs_revision",
            "type": "predicate",
            "instructions": "Does MOMENTUM_DESK have a single-leg notional breach (> $12M), stop-loss breach (> 7.5%), or require switching to CALL_SPREAD?",
        },
        {
            "name": "stat_arb_desk_needs_revision",
            "type": "predicate",
            "instructions": "Does STAT_ARB_DESK have any desk gross, stop-loss, or sector concentration breach requiring revision?",
        },
        {
            "name": "macro_vol_desk_needs_revision",
            "type": "predicate",
            "instructions": "Does MACRO_VOL_DESK have any breach or need to upsize tail hedges?",
        },
        {
            "name": "event_arb_desk_needs_revision",
            "type": "predicate",
            "instructions": "Does EVENT_ARB_DESK have any notional or stop-loss breach requiring revision?",
        },
        {
            "name": "firm_tail_fragility_score",
            "type": "score",
            "instructions": "Rate the overall tail fragility of the combined 4-desk portfolio under stressed 3.5-sigma conditions.",
            "levels": [
                {"label": "resilient", "description": "Well-hedged with low net beta and contained VaR"},
                {"label": "moderate_risk", "description": "Balanced risk within mandate tolerances"},
                {"label": "elevated_risk", "description": "Near sector or VaR limits"},
                {"label": "critical_fragility", "description": "Breaches firm mandate caps"},
            ],
        },
    ]

    decision = client.decisions.create(
        model=DECISIONS_MODEL,
        input=risk_evidence_text,
        questions=questions,  # type: ignore[arg-type]
    )
    return {
        "model": decision.model,
        "answers": _parse_decision_answers(decision),
        "usage": decision.usage.model_dump() if getattr(decision, "usage", None) else {},
    }


@traceable(run_type="llm", name="OpenAI_Decisions_API_EMS_Algo_Selector")
def select_ems_algos_via_decisions_api(
    ems_evidence_text: str,
    tickers: list[str],
) -> dict[str, Any]:
    """Use `client.decisions.create(model='gpt-6-luna')` to select optimal EMS execution algorithms and dark-pool urgency for all approved tickers in one batched call."""
    client = _get_openai_client()
    unique_tickers = list(dict.fromkeys(t.upper().strip() for t in tickers if t.strip()))[:8]
    questions: list[dict[str, Any]] = [
        {
            "name": "overall_book_execution_disposition",
            "type": "choice",
            "instructions": "Select the master execution disposition for the approved multi-desk blotter.",
            "choices": [
                {"value": "EXECUTE_ALL", "description": "Execute all approved tickets immediately via EMS router"},
                {"value": "EXECUTE_SCALED_AND_HEDGED", "description": "Execute with staged child slicing and hedges"},
                {"value": "HALT_TRADING", "description": "Halt execution due to extreme market toxicity"},
            ],
        },
    ]

    for sym in unique_tickers:
        questions.append(
            {
                "name": f"{sym}_ems_algo",
                "type": "choice",
                "instructions": f"Given Databento L1 spread bps and orderbook imbalance for {sym}, choose the optimal institutional execution algorithm.",
                "choices": [
                    {"value": "DARK_MIDPOINT_PEG_IEX_DLIMIT", "description": "Dark pool midpoint peg + IEX D-Limit to avoid spread crossing"},
                    {"value": "IS_ARRIVAL_AGGRESSIVE_SWEEP", "description": "Implementation Shortfall arrival price sweep to capture urgent alpha"},
                    {"value": "VWAP_2HR_PARTICIPATION_8PCT", "description": "2-Hour VWAP schedule capped at 8% volume participation"},
                    {"value": "PAIRS_RATIO_SPREAD_ALGO", "description": "Synchronized leg-into-leg cointegrated pairs execution algo"},
                ],
            }
        )
        questions.append(
            {
                "name": f"{sym}_adverse_selection_toxicity",
                "type": "predicate",
                "instructions": f"Does {sym} exhibit wide L1 bid-ask spreads (>15 bps) or adverse orderbook imbalance requiring passive dark-pool routing?",
            }
        )

    decision = client.decisions.create(
        model=DECISIONS_MODEL,
        input=ems_evidence_text,
        questions=questions,  # type: ignore[arg-type]
    )
    return {
        "model": decision.model,
        "answers": _parse_decision_answers(decision),
        "usage": decision.usage.model_dump() if getattr(decision, "usage", None) else {},
    }
