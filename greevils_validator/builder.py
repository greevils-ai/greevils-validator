"""Builder exclusivity: a miner must route EVERY trade through OUR Hyperliquid builder app, or be
eliminated. This is a hard PRE-GATE -- checked before any classification, eligibility or scoring
(the rule: "even one trade outside our app -> removed").

Checked REAL-TIME from `userFillsByTime` (no feed lag): a fill routed through our builder carries a
`builderFee` at our rate (BUILDER_FEE_BPS, ~5 bps). So a fill with:
  * NO builderFee            -> traded directly on HL (no builder)        -> OFF-APP
  * builderFee at a DIFFERENT rate -> went through ANOTHER builder        -> OFF-APP
  * builderFee at ~BUILDER_FEE_BPS -> went through OUR builder            -> OK
Any off-app fill eliminates the miner permanently (the caller persists an indelible DQ).

Exemptions: liquidations are forced by HL (not routed through any builder), and dust fills below
BUILDER_MIN_NOTIONAL are skipped (a builder fee on a tiny notional can round to 0, so "no fee" can't
be inferred). The one case the fee can't distinguish is a competitor builder charging the SAME rate
the miner also approved -- it would read as ours; HL's per-builder fill feed confirms that
definitively but lags ~2 days, so it's left out of this real-time path.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import NamedTuple

from .config import (
    BUILDER_EXCLUSIVITY_START,
    BUILDER_FEE_BPS,
    BUILDER_FEE_TOL_BPS,
    BUILDER_MIN_NOTIONAL,
)
from .hl_data import _day, _fills

logger = logging.getLogger(__name__)


class BuilderCheck(NamedTuple):
    """violated=True => the miner has >=1 fill that did NOT go through our builder => eliminate.
    off_app is a sample of (coin, observed_fee_bps) for the offending fills (fee_bps 0 == no fee)."""
    violated: bool
    off_app: list


def _is_liquidation(f: dict) -> bool:
    """A forced HL liquidation isn't routed through any builder (it carries no builderFee), so exempt
    it -- otherwise a liquidated miner gets permanently false-DQ'd. HL marks the LIQUIDATED user's
    own fill with a `liquidation` object (VERIFIED on real data); its `dir` stays a normal 'Close
    Long' -- only the liquidator's side reads 'Liquidated ...'. So the `liquidation` key is the
    reliable signal; the dir substring is a belt-and-suspenders secondary."""
    return "liquidation" in f or "liquidat" in str(f.get("dir", "")).lower()


def traded_outside_builder(address: str, fills: list | None = None) -> BuilderCheck:
    """Did this account place any fill OUTSIDE our builder app? Real-time, from each fill's builderFee.

    A fill is OURS iff its builderFee rate is within BUILDER_FEE_TOL_BPS of BUILDER_FEE_BPS. No fee
    (traded directly on HL) or a different rate (another builder) is off-app. `fills` may be passed
    in to avoid a re-fetch; otherwise it is fetched here.
    """
    if fills is None:
        fills = _fills(address)

    off_app: list = []
    for f in fills:
        if _is_liquidation(f):
            continue
        if BUILDER_EXCLUSIVITY_START and _day(f["time"]) < BUILDER_EXCLUSIVITY_START:
            continue  # before the rule took effect -- pre-join history isn't held against them
        notional = float(f["px"]) * float(f["sz"])
        if notional < BUILDER_MIN_NOTIONAL:
            continue  # dust: the builder fee can round to 0, so "no fee" isn't conclusive
        bf = f.get("builderFee")
        rate_bps = (float(bf) / notional * 1e4) if bf else 0.0
        if abs(rate_bps - BUILDER_FEE_BPS) > BUILDER_FEE_TOL_BPS:
            off_app.append((f["coin"], round(rate_bps, 2)))  # no fee (0) or another builder's rate

    if off_app:
        logger.info("builder-exclusivity %s: %d off-app fill(s) e.g. %s", address, len(off_app), off_app[:3])
    return BuilderCheck(bool(off_app), off_app[:5])
