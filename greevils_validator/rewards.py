"""Reward computation -- the unified PvP tournament for the Greevils validator.

`calculate_rewards(traders, emission_usd, ever_open_sourced)` is called once per round
with every trader competing this round, each tagged "agent"/"human" and (for agents)
whether its code is open-sourced. It runs ONE pairwise tournament over all of them
(agents and humans play each other), then splits emissions into an agent pool and a
human pool -- the human pool sized by the dollar cap -- and returns one non-negative
weight per trader (SAME ORDER). The weights sum to <= 1; the caller burns the remainder.

Agent lane is alive from day one: before any agent open-sources, closed-source agents earn
with NO eligibility gate (just elimination + measurable); once any agent open-sources (the
latch), only open-source agents earn (full gate). Humans are never eligibility-gated.

Pipeline (see tournament.py):
  * Each address's full daily series is fetched, then gated by the LIFETIME
    eligibility + elimination rules (scoring.py). Failing either drops the trader
    from the tournament (weight 0).
  * Every surviving pair -- including human-vs-agent -- is scored on its shared
    overlap window (the factor engine in scoring.py: U -> G -> M), plus a decayed
    longevity bonus crediting the older trader's pre-overlap track record onto R/X.
  * The winner of each pair accrues (S_win - S_loser); margins are summed across all
    pairs into one unified score, which sizes/splits both pools.

Data source: the validator's own daily DB (db.py), which records one exact row per
account per UTC day by snapshotting the Hyperliquid native API at the day boundary
(end-of-day balance, net flow as a residual, executed volume; "active" = volume > 0)
plus the coins traded (for the spot / off-whitelist DQ). Days it couldn't snapshot live are
backfilled by reconstruction (hl_data.py). The validator SCORES from this store.
"""
import logging
import math
from collections import Counter

from . import db
from .config import DB_PATH
from .tournament import MinerData, tournament_weights

logger = logging.getLogger(__name__)


def _fetch_miner_data(address: str, account_type: str) -> MinerData:
    """Bring the validator's daily DB current for this account, then return the stored series.

    Raises on a network/API failure; calculate_rewards catches it and scores the
    address 0 (so one bad fetch never aborts the round)."""
    conn = db.connect(DB_PATH)
    try:
        db.update_account(conn, address, account_type)
        return db.load_miner_data(conn, address, account_type)
    finally:
        conn.close()


def calculate_rewards(
    traders: list[tuple[str, str, bool]],
    emission_usd: float | None,
    ever_open_sourced: bool = False,
) -> list[float]:
    """Score every trader via the unified PvP tournament. Returns one weight per
    trader, in order; weights sum to <= 1 (the caller burns the remainder).

    ``traders`` is a list of (hl_address, account_type, open_sourced): account_type is
    "agent" or "human" (sets eligibility floors + which pool), and open_sourced flags an
    agent that has published its code. Everyone competes in the SAME tournament.
    ``emission_usd`` is the USD value of the round's emission (pricing.py), sizing the
    human pool; None -> human cap 0 this round. ``ever_open_sourced`` is the persistent
    open-source latch -- once set, only open-source agents earn the agent lane.

    Robust by construction: an address whose data can't be fetched enters with no
    records, is dropped by the eligibility gate, and scores 0 -- it never aborts
    the round. An all-zero output is valid and makes the caller burn everything.
    """
    miners: list[MinerData] = []
    for addr, account_type, open_sourced in traders:
        try:
            md = _fetch_miner_data(addr, account_type)
        except Exception as exc:
            logger.warning("data fetch failed for %s: %s -- scoring 0", addr, exc)
            md = MinerData(addr, [], account_type)
        md.open_sourced = bool(open_sourced)
        miners.append(md)

    weight_map = tournament_weights(miners, emission_usd, ever_open_sourced)

    # The tournament scores a duplicate address once; split that single weight evenly
    # across its positions so a double-claim can't capture inflated share.
    counts = Counter(addr for addr, *_ in traders)
    dupes = {a for a, n in counts.items() if n > 1}
    if dupes:
        logger.warning("duplicate address(es) in round, splitting weight: %s", dupes)

    # Sanitize at the contract boundary: a NaN/inf weight (e.g. from garbage equity in
    # the data layer) would poison the caller's weight vector, so only finite > 0 counts.
    rewards: list[float] = []
    for addr, *_ in traders:
        w = weight_map.get(addr, 0.0) / counts[addr]
        rewards.append(w if math.isfinite(w) and w > 0.0 else 0.0)

    if not any(rewards):
        logger.warning(
            "calculate_rewards: all zeros (round will burn) -- no eligible, "
            "non-eliminated winners this round"
        )
    return rewards
