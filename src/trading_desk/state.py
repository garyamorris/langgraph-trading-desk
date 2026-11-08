"""State definitions, reducers, and strict Pydantic schemas for the 24-Node Hierarchical Multi-Strategy Hedge Fund."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer that safely merges dictionaries from parallel LangGraph branches or subgraphs."""
    merged = dict(left or {})
    if right:
        merged.update(right)
    return merged


# ============================================================================
# TIER 0: CIO REGIME ROUTER & DYNAMIC TICKER DISPATCH
# ============================================================================

class CIORegimeMandate(BaseModel):
    """Chief Investment Officer's top-level strategic dispatch & capital budget."""

    session_regime: str = Field(description="Current market regime & session (e.g., PRE_MARKET_VOL_EXPANSION, INTRADAY_DISPERSION)")
    focus_tickers: list[str] = Field(description="5 to 7 tickers selected for deep parallel single-name research via Send()")
    active_desks: list[Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"]] = Field(
        description="Strategy desks activated for this trading cycle"
    )
    target_firm_gross_utilization_pct: float = Field(
        description="Target post-trade gross exposure % of $100M NAV (e.g., 95.0 to 110.0)"
    )
    strategic_priorities: str = Field(
        description="CIO's strategic mandate for the Intelligence Syndicate, Strategy Desks, and CRO"
    )


# ============================================================================
# TIER 1: 6-WAY PARALLEL INTELLIGENCE SYNDICATE + MAP-REDUCE DEEP WORKER
# ============================================================================

class PremarketBrief(BaseModel):
    """Pre-market & Databento L1 orderbook microstructure brief."""

    market_session: str = Field(description="Current session state")
    focus_tickers: list[str] = Field(description="Tickers analyzed")
    overnight_gaps_summary: str = Field(description="Alpaca overnight gaps, VWAP distance, and relative volume")
    microstructure_summary: str = Field(description="Databento mbp-1 L1 orderbook imbalance, spreads in bps, and sweep flow")
    conviction_bias: Literal["BULLISH", "BEARISH", "NEUTRAL", "DISPERSION"] = Field(description="Pre-market bias")


class TickerTechnicalSignal(BaseModel):
    """Technical signal summary for a single ticker."""

    ticker: str = Field(description="Ticker symbol")
    technical_regime: str = Field(description="Regime classification")
    signal_summary: str = Field(description="RSI(14), MACD histogram, Bollinger %B, Z-score, ATR(14), and support/resistance")


class TechnicalSignalReport(BaseModel):
    """Quantitative technical analysis across focus tickers."""

    ticker_signals: list[TickerTechnicalSignal] = Field(description="Technical readouts per ticker")
    top_momentum_long: str = Field(description="Strongest technical breakout setup")
    top_mean_reversion_or_short: str = Field(description="Strongest mean-reversion or short setup")
    volatility_regime: Literal["LOW_VOL_GRIND", "NORMAL", "HIGH_VOL_EXPANSION", "EXTREME_STRESS"] = Field(
        description="Realized volatility regime"
    )


class MacroRegimeReport(BaseModel):
    """Cross-asset macro & factor rotation regime."""

    macro_regime: Literal["GOLDILOCKS_RISK_ON", "REFLATION", "STAGFLATION_RISK_OFF", "RATES_SHOCK", "DEFENSIVE_ROTATION"] = Field(
        description="Current macro classification"
    )
    rates_and_dollar_view: str = Field(description="Analysis of Treasury duration (TLT), Credit (HYG), and Gold (GLD)")
    sector_leadership: list[str] = Field(description="Leading ETFs/sectors")
    sector_laggards: list[str] = Field(description="Lagging ETFs/sectors")
    recommended_gross_leverage_scalar: float = Field(description="Suggested leverage scalar (0.5 to 1.25)")


class TickerSentimentScore(BaseModel):
    """Sentiment score and catalyst summary for a single ticker."""

    ticker: str = Field(description="Ticker symbol")
    sentiment_score: float = Field(description="Sentiment score from -1.0 to +1.0")
    catalyst_note: str = Field(description="Primary breaking news driver")


class NewsSentimentReport(BaseModel):
    """Real-time Alpaca breaking news & catalyst sentiment digest."""

    overall_sentiment: Literal["BULLISH", "SLIGHTLY_BULLISH", "NEUTRAL", "SLIGHTLY_BEARISH", "BEARISH"] = Field(
        description="Aggregate news sentiment"
    )
    ticker_sentiments: list[TickerSentimentScore] = Field(description="Per-ticker sentiment scores")
    key_catalysts_and_headlines: list[str] = Field(description="Top breaking headlines from Alpaca News API")
    event_risk_warnings: list[str] = Field(description="Imminent earnings, regulatory, or macro event risks")


class TickerVolSurfaceSignal(BaseModel):
    """Implied vs realized volatility & options skew readout for a single ticker."""

    ticker: str = Field(description="Ticker symbol")
    iv_rv_spread_vol_pts: float = Field(description="Implied vol minus 20d realized vol in vol points")
    put_call_skew_regime: str = Field(description="Skew regime (e.g., STEEP_PUT_SKEW, CALL_BID_MELTUP, BALANCED)")
    optimal_options_structure: str = Field(description="Recommended structure (e.g., CALL_SPREAD, PUT_SPREAD, COLLAR, EQUITY)")


class VolSurfaceReport(BaseModel):
    """Options Volatility Surface, Skew & Gamma Dealer Positioning report."""

    dealer_gamma_regime: Literal["LONG_GAMMA_PINNING", "NEUTRAL_GAMMA", "SHORT_GAMMA_ACCELERATION"] = Field(
        description="Aggregate index/single-name dealer gamma positioning"
    )
    ticker_vol_signals: list[TickerVolSurfaceSignal] = Field(description="Vol surface & skew signals by ticker")
    top_vol_arbitrage_setup: str = Field(description="Best cheap-convexity or rich-premium setup")


class PairCointegrationCandidate(BaseModel):
    """Statistical arbitrage pair cointegration & correlation candidate."""

    long_leg: str = Field(description="Undervalued / oversold long ticker")
    short_leg: str = Field(description="Overvalued / sector hedge short ticker")
    rolling_correlation_60d: float = Field(description="60-day return correlation")
    spread_z_score_20d: float = Field(description="20-day relative spread Z-score")
    half_life_days: float = Field(description="Estimated mean-reversion half-life in days")


class FactorCointegrationReport(BaseModel):
    """Cross-sectional correlation matrix, PCA factor crowding & cointegration report."""

    dominant_pca_factor_explained_pct: float = Field(description="% variance explained by PC1 market/tech factor")
    crowding_warning: str = Field(description="Factor crowding & correlation breakdown assessment")
    top_cointegrated_pairs: list[PairCointegrationCandidate] = Field(description="Top statistical arbitrage pairs")


class TickerDeepResearchDossier(BaseModel):
    """Single-ticker deep research dossier produced via dynamic Send() map-reduce worker."""

    ticker: str = Field(description="Ticker symbol")
    composite_alpha_score: float = Field(description="Composite quantitative + catalyst alpha score (-10.0 to +10.0)")
    live_spot_price: float = Field(description="Current live Alpaca spot price")
    atr_14_usd: float = Field(description="14-day ATR in USD")
    microstructure_obi: float = Field(description="Databento L1 orderbook imbalance (-1 to +1)")
    preferred_direction: Literal["LONG", "SHORT", "NEUTRAL_PAIR"] = Field(description="Recommended directional bias")
    preferred_instrument: Literal["EQUITY", "CALL_SPREAD", "PUT_SPREAD", "COLLAR", "PAIR_LEG"] = Field(
        description="Optimal instrument given vol surface & news event risk"
    )
    suggested_entry: float = Field(description="Suggested entry price")
    suggested_target: float = Field(description="Suggested target price")
    suggested_stop_within_7pct: float = Field(description="Hard stop-loss strictly within 7.0% of entry")
    synthesis_note: str = Field(description="Concise synthesis of tape, technicals, news, and microstructure for this ticker")
    # Filled in by the graph (never by the LLM): hidden from the structured-output JSON schema.
    openai_decisions_api: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TIER 2: ADVERSARIAL BULL / BEAR / CIO DEBATE SCHEMAS
# ============================================================================

class DebateArgument(BaseModel):
    """Structured thesis or counter-thesis in the Adversarial Investment Debate."""

    speaker: Literal["BULL_RESEARCH_DIRECTOR", "BEAR_SHORT_SELLER"] = Field(description="Debate participant")
    core_thesis: str = Field(description="Primary macro, technical, and microstructure argument")
    high_conviction_trades: list[str] = Field(description="Specific long or short trade recommendations with levels")
    critique_of_opponent: str = Field(description="Sharp quantitative critique of the opposing side's blind spots")


class AdversarialDebateResolution(BaseModel):
    """CIO Debate Arbiter's synthesis of the Bull vs Bear thesis debate."""

    winning_tilt: Literal["NET_LONG_SELECTIVE", "MARKET_NEUTRAL_DISPERSION", "DEFENSIVE_HEDGED"] = Field(
        description="Resolved firm-wide directional tilt after weighing Bull vs Bear arguments"
    )
    bull_valid_points: list[str] = Field(description="Validated alpha opportunities from the Bull Director")
    bear_valid_risks: list[str] = Field(description="Validated tail risks & short opportunities from the Bear Short-Seller")
    desk_allocation_weights_pct: list[str] = Field(
        description="Recommended capital weightings across MOMENTUM_DESK, STAT_ARB_DESK, MACRO_VOL_DESK, EVENT_ARB_DESK"
    )
    cio_synthesis_memo: str = Field(description="Authoritative synthesis guiding the 4 Strategy Desk Subgraphs")
    openai_decisions_api: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TIER 3: MULTI-AGENT STRATEGY DESK SUBGRAPH SCHEMAS (4 DESKS)
# ============================================================================

class StrategyOrderLeg(BaseModel):
    """Individual trade leg proposed by a strategy desk."""

    desk: Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"] = Field(
        description="Originating strategy desk"
    )
    strategy_type: str = Field(description="Specific strategy archetype")
    ticker: str = Field(description="Ticker symbol (e.g., NVDA, AMD, MSFT, AAPL, SMH, QQQ, SPY, TLT)")
    direction: Literal["LONG", "SHORT"] = Field(description="LONG or SHORT")
    instrument: Literal["EQUITY", "CALL_SPREAD", "PUT_SPREAD", "COLLAR", "PAIR_LEG"] = Field(
        description="Execution instrument structure"
    )
    notional_usd: float = Field(description="Proposed dollar notional in USD")
    entry_price: float = Field(description="Target entry price in USD")
    target_price: float = Field(description="Target take-profit price in USD")
    stop_loss: float = Field(description="Hard stop-loss price in USD")
    holding_period: str = Field(description="Expected holding horizon")
    conviction: Literal["LOW", "MEDIUM", "HIGH"] = Field(description="Conviction level")
    rationale: str = Field(description="Quantitative & fundamental justification")


class DeskQuantModelOutput(BaseModel):
    """Internal output from a Strategy Desk's Quant Modeler & Structurer before Desk Head sign-off."""

    desk_name: Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"] = Field(
        description="Strategy desk"
    )
    quant_signal_rationale: str = Field(description="Mathematical signal generation & options/pair structuring rationale")
    candidate_legs: list[StrategyOrderLeg] = Field(description="Candidate trade legs structured by the Desk Quant")


class DeskMandateProposal(BaseModel):
    """Final trade book signed off by a Strategy Desk Head (output of the Desk Subgraph)."""

    desk_name: Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"] = Field(
        description="Desk identifier"
    )
    desk_head_summary: str = Field(description="Desk Head's executive thesis and risk-reward sign-off")
    total_gross_notional_usd: float = Field(description="Sum of absolute notional across proposed legs")
    total_net_notional_usd: float = Field(description="Net directional dollar exposure (Longs - Shorts)")
    proposed_legs: list[StrategyOrderLeg] = Field(description="1 to 3 concrete trade legs for this strategy desk")
    revision_adjustments: str = Field(
        default="",
        description="Exact sizing, hedging, or stop-loss changes made in response to Risk Committee directives",
    )


# ============================================================================
# TIER 4: TRIAD RISK & EXPOSURE COMMITTEE SCHEMAS
# ============================================================================

class FactorExposureAudit(BaseModel):
    """Factor & Sector Concentration Auditor report."""

    post_trade_gross_pct_nav: float = Field(description="Post-trade firm gross exposure % of $100M NAV")
    post_trade_net_pct_nav: float = Field(description="Post-trade firm net exposure % of $100M NAV")
    net_beta_spy_equivalent_usd: float = Field(description="Beta-weighted net SPY-equivalent dollar exposure")
    sector_exposure_summary: str = Field(description="Post-trade net exposure across Semiconductors, Software, Tech, Macro")
    concentration_breaches: list[str] = Field(description="List of single-leg, desk-gross, or sector concentration breaches")


class TailRiskMonteCarloReport(BaseModel):
    """Tail-Risk, VaR & Monte Carlo Stress Tester report."""

    parametric_var_99_1d_usd: float = Field(description="Diversified 99% 1-day parametric VaR in USD")
    expected_shortfall_cvar_99_usd: float = Field(description="99% Conditional VaR / Expected Shortfall in USD")
    stressed_regime_var_usd: float = Field(description="3.5-sigma correlation-breakdown stressed VaR in USD")
    scenario_shocks_summary: str = Field(
        description="P&L impact under AI Bubble Unwind (-12% Semis), Rates Shock (+50bps), and Liquidity Crunch"
    )
    var_and_stop_breaches: list[str] = Field(description="List of VaR or stop-loss distance breaches")


class DeskRiskDirective(BaseModel):
    """Targeted risk directive issued by the CRO to a specific strategy desk."""

    desk_name: Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"] = Field(
        description="Target strategy desk"
    )
    must_revise: bool = Field(description="True if this specific desk has a breach or must adjust hedges")
    directive: str = Field(description="Exact numerical sizing, stop-loss, or structure changes required")


class PortfolioRiskReport(BaseModel):
    """Master CRO Risk Committee synthesis across Factor Audit & Tail-Risk Stress Test."""

    status: Literal["APPROVED", "REVISE_DESKS", "REJECTED"] = Field(
        description="APPROVED if zero breaches; REVISE_DESKS if selective desk revisions needed; REJECTED if unresolvable"
    )
    risk_score: int = Field(description="Firm-wide risk score from 1 (low) to 10 (critical)")
    combined_gross_exposure_pct_nav: float = Field(description="Post-trade firm gross exposure % of NAV")
    combined_net_exposure_pct_nav: float = Field(description="Post-trade firm net exposure % of NAV")
    portfolio_var_99_1d_usd: float = Field(description="Diversified 99% 1-day VaR in USD")
    stressed_var_99_usd: float = Field(description="3.5-sigma stressed VaR in USD")
    net_beta_adjusted_exposure_usd: float = Field(description="SPY-equivalent beta-weighted net exposure in USD")
    limit_breaches: list[str] = Field(default_factory=list, description="All active mandate breaches")
    desk_directives: list[DeskRiskDirective] = Field(
        default_factory=list,
        description="Directives per desk with must_revise flag for selective Send() re-dispatch",
    )
    cro_commentary: str = Field(description="Executive CRO synthesis of Factor Audit and Monte Carlo Tail Stress")
    openai_decisions_api: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TIER 5: HEAD OF TRADING & MICROSTRUCTURE EMS ROUTER SCHEMAS
# ============================================================================

class DeskCapitalAllocation(BaseModel):
    """Approved gross and net capital allocation for a single strategy desk."""

    desk_name: Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"] = Field(
        description="Strategy desk"
    )
    approved_gross_usd: float = Field(description="Approved gross dollar capital in USD")
    approved_net_usd: float = Field(description="Approved net directional dollar capital in USD")


class ApprovedExecutionTicket(BaseModel):
    """Execution ticket authorized by the Global Head of Trading."""

    desk: str = Field(description="Originating desk")
    ticker: str = Field(description="Ticker symbol")
    direction: Literal["LONG", "SHORT"] = Field(description="LONG or SHORT")
    instrument: str = Field(description="Instrument type")
    approved_notional_usd: float = Field(description="Final allocated notional in USD")
    limit_or_entry_price: float = Field(description="Execution reference/limit price")
    target_price: float = Field(description="Take-profit price")
    stop_loss: float = Field(description="Hard stop-loss price")
    algo_routing: str = Field(description="Initial execution algorithm")


class HeadOfTradingDecision(BaseModel):
    """Master capital allocation decision from the Global Head of Trading."""

    overall_decision: Literal["EXECUTE_ALL", "EXECUTE_SCALED_AND_HEDGED", "HALT_TRADING"] = Field(
        description="Master execution decision"
    )
    total_deployed_gross_usd: float = Field(description="Total gross capital deployed in USD")
    total_deployed_net_usd: float = Field(description="Total net directional capital deployed in USD")
    desk_allocations: list[DeskCapitalAllocation] = Field(description="Approved capital allocation per desk")
    execution_blotter: list[ApprovedExecutionTicket] = Field(description="Approved execution tickets")
    executive_summary: str = Field(description="Head of Trading's synthesis memo")


class ChildOrderSliceSchedule(BaseModel):
    """Microstructure-optimized child order slicing & TCA schedule for a single parent ticket."""

    parent_desk: str = Field(description="Originating strategy desk")
    ticker: str = Field(description="Ticker symbol")
    direction: str = Field(description="LONG or SHORT")
    instrument: str = Field(description="Instrument structure")
    total_notional_usd: float = Field(description="Total parent order notional in USD")
    estimated_shares_or_contracts: int = Field(description="Estimated shares or option contracts")
    selected_ems_algo: str = Field(
        description="Specific EMS algorithm (e.g., 'DARK_MIDPOINT_PEG + IEX_D_LIMIT', 'IS_ARRIVAL_AGGRESSIVE_SWEEP', 'VWAP_2HR_PARTICIPATION_8PCT')"
    )
    venue_routing_split: str = Field(
        description="Venue allocation (e.g., '45% Dark Pools (SigmaX/Crossfinder), 35% IEX Speedbump, 20% Lit NYSE/ARCA')"
    )
    num_child_slices: int = Field(description="Number of time/liquidity child slices")
    expected_slippage_bps: float = Field(description="Estimated market impact + spread cost in bps")
    alpaca_order_payload_preview: str = Field(
        description="Ready-to-submit Alpaca v2/orders JSON payload preview (symbol, qty, side, type, time_in_force)"
    )


class ExecutionMicrostructurePlan(BaseModel):
    """Final output from the Quantitative Execution & Microstructure EMS Router."""

    total_parent_orders: int = Field(description="Number of parent tickets sliced")
    total_expected_tca_cost_usd: float = Field(description="Estimated total transaction cost (slippage + spread) in USD")
    child_order_schedules: list[ChildOrderSliceSchedule] = Field(
        description="Microstructure-optimized child order slicing schedule per ticket"
    )
    ems_execution_commentary: str = Field(
        description="Execution Desk Head's commentary on Databento L1 spread capture, dark pool toxicity avoidance, and order staging"
    )
    openai_decisions_api: SkipJsonSchema[dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# MASTER GRAPH & SUBGRAPH STATES
# ============================================================================

class TickerWorkerState(TypedDict):
    """Input state sent dynamically via Send('ticker_deep_research_worker', ...) for each focus ticker."""

    ticker: str


DeskName = Literal["MOMENTUM_DESK", "STAT_ARB_DESK", "MACRO_VOL_DESK", "EVENT_ARB_DESK"]


class DeskInput(TypedDict):
    """Payload the parent sends (via `Send`) to the desk subgraph: everything one desk needs, nothing else."""

    desk_name: DeskName
    revision_count: int
    context: str  # prompt context prebuilt by the parent from its own state


class DeskOutput(TypedDict):
    """What the desk subgraph hands back to the parent (reducers live on the parent's channels)."""

    desk_proposals: dict[str, DeskMandateProposal]
    messages: list[AnyMessage]


class DeskState(DeskInput):
    """Private working state of the desk subgraph; `quant_draft` never leaves it."""

    quant_draft: DeskQuantModelOutput | None


# Values in the dict channels are Pydantic models, not dumped dicts. Reducer channels are only
# those where parallel branches write to the same key; everything else is a plain last-value channel.
class TradingDeskState(TypedDict):
    """Shared state across the 24-node Hierarchical Multi-Strategy Hedge Fund graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    focus_tickers: list[str]
    # Tier 0: CIO Mandate
    cio_mandate: CIORegimeMandate | None
    # Tier 1: Parallel Intelligence Syndicate (keyed by analyst) + Send() ticker dossiers (keyed by ticker)
    intel_reports: Annotated[dict[str, BaseModel], merge_dicts]
    ticker_dossiers: Annotated[dict[str, TickerDeepResearchDossier], merge_dicts]
    # Tier 2: Adversarial Bull / Bear / CIO Debate (keys: bull_case, bear_case, resolution)
    adversarial_debate: Annotated[dict[str, BaseModel], merge_dicts]
    # Tier 3: one entry per strategy desk, written by the desk subgraph
    desk_proposals: Annotated[dict[str, DeskMandateProposal], merge_dicts]
    # Tier 4: Triad Risk & Exposure Committee (keys: factor_audit, tail_var_mc)
    risk_sub_reports: Annotated[dict[str, BaseModel], merge_dicts]
    risk_assessment: PortfolioRiskReport | None
    # Tier 5: Head of Trading & Execution Microstructure EMS Router
    head_of_trading_decision: HeadOfTradingDecision | None
    execution_microstructure_plan: ExecutionMicrostructurePlan | None
    # Iterative Loop Controls
    revision_count: int
    max_revisions: int
