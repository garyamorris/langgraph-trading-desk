"""Offline wiring tests for the trading-desk graph.

Every LLM, market-data tool and Decisions-API call is stubbed, so these run with no network, no API keys
and no cost. The deterministic risk engine (`evaluate_multi_desk_book_risk`) is the real one.

Run:  uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import unittest
import uuid
from pathlib import Path
from typing import Any, Literal, get_args, get_origin
from unittest import mock

import langsmith
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import trading_desk.graph as g
from trading_desk.state import (
    AdversarialDebateResolution,
    CIORegimeMandate,
    DeskMandateProposal,
    ExecutionMicrostructurePlan,
    HeadOfTradingDecision,
    PortfolioRiskReport,
    StrategyOrderLeg,
    TickerDeepResearchDossier,
)

ALL_DESKS = g.ALL_DESKS
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

def _default_for(annotation: Any) -> Any:
    if get_origin(annotation) is Literal:
        return get_args(annotation)[0]
    origin = get_origin(annotation) or annotation
    return {str: "x", float: 0.0, int: 0, bool: False, list: [], dict: {}}[origin]


def stub(model, **overrides):
    """Build a valid instance of `model`, filling every required field with a placeholder."""
    values = {
        name: _default_for(field.annotation)
        for name, field in model.model_fields.items()
        if field.is_required() and name not in overrides
    }
    return model(**values, **overrides)


def leg(desk: str, ticker: str, direction: str, notional: float, instrument: str = "EQUITY") -> StrategyOrderLeg:
    return stub(
        StrategyOrderLeg,
        desk=desk,
        ticker=ticker,
        direction=direction,
        instrument=instrument,
        notional_usd=notional,
        entry_price=100.0,
        target_price=110.0,
        stop_loss=95.0,
    )


COMPLIANT_BOOKS = {
    "MOMENTUM_DESK": [("MSFT", "LONG", 3e6, "EQUITY")],
    "STAT_ARB_DESK": [("AMD", "LONG", 3e6, "EQUITY"), ("SMH", "SHORT", 3e6, "EQUITY")],
    "MACRO_VOL_DESK": [("QQQ", "SHORT", 3e6, "EQUITY")],
    "EVENT_ARB_DESK": [("TLT", "LONG", 2e6, "CALL_SPREAD")],
}


class Scenario:
    """Controls what the stubbed desk heads / CRO language model say."""

    def __init__(self, desk_books=None, llm_cro_status="APPROVED"):
        # desk_books(desk, rev) -> list[StrategyOrderLeg]
        self.desk_books = desk_books or (lambda desk, rev: compliant(desk))
        self.llm_cro_status = llm_cro_status
        self.desk_head_calls: list[tuple[str, int]] = []
        self.node_trace: list[str] = []


def compliant(desk: str) -> list[StrategyOrderLeg]:
    return [leg(desk, t, d, n, i) for t, d, n, i in COMPLIANT_BOOKS[desk]]


class FakeStructured:
    def __init__(self, schema, scenario: Scenario):
        self.schema = schema
        self.scenario = scenario

    def invoke(self, messages):
        schema, sc = self.schema, self.scenario
        text = str(messages[-1].content)
        if schema is DeskMandateProposal:
            desk, rev = re.search(r"TARGET STRATEGY DESK: (\w+) \(Revision Round: (\d+)\)", text).groups()
            sc.desk_head_calls.append((desk, int(rev)))
            return stub(DeskMandateProposal, desk_name=desk, proposed_legs=sc.desk_books(desk, int(rev)))
        if schema.__name__ == "DeskQuantModelOutput":
            desk = re.search(r"TARGET STRATEGY DESK: (\w+)", text).group(1)
            return stub(schema, desk_name=desk)
        if schema is PortfolioRiskReport:
            return stub(schema, status=sc.llm_cro_status)
        if schema is CIORegimeMandate:
            return stub(schema, focus_tickers=["NVDA", "AMD", "MSFT", "SPY", "QQQ", "TLT"], active_desks=ALL_DESKS)
        if schema is HeadOfTradingDecision:
            return stub(schema, overall_decision="EXECUTE_ALL")  # empty blotter -> graph stages the tickets
        return stub(schema)


class FakeLLM:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="ok")  # no tool_calls -> tool loop ends immediately

    def with_structured_output(self, schema):
        return FakeStructured(schema, self.scenario)


class FakeTool:
    def invoke(self, args):
        return {}


def run_graph(scenario: Scenario, max_revisions: int = 2, checkpointer=None, thread_id: str | None = None):
    patches = [
        mock.patch.object(g, "_get_llm", lambda temperature=None: FakeLLM(scenario)),
        mock.patch.object(g, "fetch_alpaca_live_snapshots", FakeTool()),
        mock.patch.object(g, "compute_technical_indicators", FakeTool()),
        mock.patch.object(g, "fetch_databento_orderbook_microstructure", FakeTool()),
        mock.patch.object(g, "get_firm_portfolio_and_mandate", FakeTool()),
        mock.patch.object(g, "evaluate_ticker_via_decisions_api", lambda **kw: {"answers": {}}),
        mock.patch.object(g, "judge_debate_via_decisions_api", lambda **kw: {"answers": {}}),
        mock.patch.object(g, "evaluate_cro_gate_via_decisions_api", lambda **kw: {"answers": {}}),
        mock.patch.object(g, "select_ems_algos_via_decisions_api", lambda **kw: {"answers": {}}),
    ]
    graph = g.build_trading_desk_graph(checkpointer=checkpointer)
    config = {"recursion_limit": 60, "configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
    initial = {
        "messages": [HumanMessage(content="Run the desk.")],
        "focus_tickers": ["NVDA", "AMD", "MSFT", "SPY", "QQQ", "TLT"],
        "revision_count": 0,
        "max_revisions": max_revisions,
    }
    for p in patches:
        p.start()
    try:
        with langsmith.tracing_context(enabled=False):  # never ship stub runs to LangSmith
            final = graph.invoke(initial, config)
    finally:
        for p in reversed(patches):
            p.stop()
    return graph, config, final


def sent_by(final: dict[str, Any]) -> list[str]:
    return [m.name for m in final["messages"] if getattr(m, "name", None)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class GraphWiringTests(unittest.TestCase):
    def test_approved_book_flows_to_execution_with_typed_state(self):
        sc = Scenario()
        _, _, final = run_graph(sc)

        self.assertEqual(final["risk_assessment"].status, "APPROVED")
        self.assertEqual(set(final["desk_proposals"]), set(ALL_DESKS))
        for proposal in final["desk_proposals"].values():
            self.assertIsInstance(proposal, DeskMandateProposal)
        self.assertIsInstance(final["head_of_trading_decision"], HeadOfTradingDecision)
        self.assertIsInstance(final["execution_microstructure_plan"], ExecutionMicrostructurePlan)
        self.assertEqual(final["execution_microstructure_plan"].total_parent_orders, 5)
        self.assertNotIn("CRO_Trading_Halt", sent_by(final))
        self.assertEqual(final["revision_count"], 0)
        # Each desk ran exactly once.
        self.assertEqual(sorted(sc.desk_head_calls), sorted((d, 0) for d in ALL_DESKS))
        # The desk subgraph's private draft must not leak into the parent state.
        self.assertNotIn("quant_draft", final)
        self.assertNotIn("desk_quant_Drafts", final)

    def test_only_flagged_desks_are_redispatched(self):
        def books(desk, rev):
            if desk == "MOMENTUM_DESK" and rev == 0:
                return [leg(desk, "NVDA", "LONG", 14e6)]  # single-leg cap + VaR breach on first pass
            return compliant(desk)

        sc = Scenario(desk_books=books)
        _, _, final = run_graph(sc)

        round0 = {d for d, r in sc.desk_head_calls if r == 0}
        round1 = {d for d, r in sc.desk_head_calls if r == 1}
        self.assertEqual(round0, set(ALL_DESKS))
        self.assertEqual(round1, {"MOMENTUM_DESK", "MACRO_VOL_DESK"})  # the two desks the risk engine flagged
        self.assertEqual(final["risk_assessment"].status, "APPROVED")
        self.assertEqual(final["revision_count"], 1)
        self.assertIsNotNone(final["execution_microstructure_plan"])

    def test_unresolved_breach_fails_closed_even_if_llm_says_approved(self):
        # Three 8M legs = $24M desk gross > $22M cap. The revision clamp (8.5M/leg) cannot fix a gross breach.
        def books(desk, rev):
            if desk == "MOMENTUM_DESK":
                return [leg(desk, t, "LONG", 8e6) for t in ("MSFT", "SPY", "TLT")]
            return compliant(desk)

        # The language-model CRO says APPROVED; the deterministic engine must overrule it.
        sc = Scenario(desk_books=books, llm_cro_status="APPROVED")
        _, _, final = run_graph(sc, max_revisions=2)

        self.assertEqual(final["risk_assessment"].status, "REJECTED")
        self.assertEqual(final["revision_count"], 2)
        self.assertTrue(final["risk_assessment"].limit_breaches)
        self.assertIsNone(final.get("head_of_trading_decision"))
        self.assertIsNone(final.get("execution_microstructure_plan"))
        self.assertEqual(sent_by(final)[-1], "CRO_Trading_Halt")
        self.assertNotIn("Head_Of_Trading", sent_by(final))

    def test_typed_state_survives_checkpoint_roundtrip(self):
        # Use exactly the allowlist langgraph.json gives the dev server, so this also holds in strict-msgpack mode.
        with open(ROOT / "langgraph.json") as fh:
            allowed = [tuple(m) for m in json.load(fh)["serde"]["allowed_msgpack_modules"]]
        saver = InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=allowed))
        graph, config, _ = run_graph(Scenario(), checkpointer=saver)
        values = graph.get_state(config).values  # deserialised from the checkpointer

        # Every Pydantic model that lands in a state channel must be on that allowlist.
        stored = set()
        for value in values.values():
            for item in value.values() if isinstance(value, dict) else [value]:
                if isinstance(item, BaseModel):
                    stored.add((type(item).__module__, type(item).__name__))
        self.assertTrue(stored)
        self.assertLessEqual(stored, set(allowed))

        self.assertIsInstance(values["cio_mandate"], CIORegimeMandate)
        self.assertIsInstance(values["ticker_dossiers"]["NVDA"], TickerDeepResearchDossier)
        self.assertIsInstance(values["adversarial_debate"]["resolution"], AdversarialDebateResolution)
        self.assertIsInstance(values["risk_assessment"], PortfolioRiskReport)
        self.assertIsInstance(values["execution_microstructure_plan"], ExecutionMicrostructurePlan)

    def test_decisions_field_is_hidden_from_llm_schemas(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool

        for model in (TickerDeepResearchDossier, AdversarialDebateResolution, PortfolioRiskReport, ExecutionMicrostructurePlan):
            props = convert_to_openai_tool(model)["function"]["parameters"]["properties"]
            self.assertNotIn("openai_decisions_api", props, model.__name__)


if __name__ == "__main__":
    unittest.main()
