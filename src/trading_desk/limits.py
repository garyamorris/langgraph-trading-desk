"""Single source of truth for the firm's risk numbers.

`MANDATE_LIMITS` are the hard caps the deterministic risk engine enforces.
`REVISION_TARGETS` are deliberately tighter numbers handed to desks on a revision round and enforced by the
revision clamp, so a revised book clears the engine with headroom.

The engine, the clamp, the prompts and the CRO messages all read from here, so a number is changed in one place.
"""

from __future__ import annotations

MANDATE_LIMITS: dict[str, float] = {
    "max_single_leg_notional_usd": 12_000_000,
    "max_desk_gross_notional_usd": 22_000_000,
    "max_post_trade_firm_gross_pct_nav": 125.0,
    "max_post_trade_firm_net_pct_nav": 40.0,
    "max_sector_net_exposure_pct_nav": 28.0,
    "max_portfolio_var_99_1d_usd": 1_450_000,
    "max_stop_loss_distance_pct": 7.5,
}

REVISION_TARGETS: dict[str, float] = {
    "max_single_leg_notional_usd": 9_000_000,
    "max_desk_gross_notional_usd": 18_000_000,
    "max_stop_loss_distance_pct": 6.2,
}


def fmt_usd(value: float) -> str:
    """`9000000` -> `$9,000,000`."""
    return f"${value:,.0f}"


def fmt_pct(value: float) -> str:
    """`28.0` -> `28%`, `6.2` -> `6.2%`."""
    return f"{value:g}%"
