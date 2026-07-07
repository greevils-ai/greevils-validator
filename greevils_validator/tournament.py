"""Unified PvP tournament + longevity + the human-arena cap for the Greevils subnet.

The per-account factor math lives in scoring.py (utility U -> transform G ->
punishment M). This module wraps it in the PAIRWISE tournament that distributes
emissions:

  * ONE tournament over ALL eligible miners -- agents AND humans, cross-type pairs
    included -- so each score reflects the whole field.
  * A pair (A, B) is scored on their OVERLAP window [max(start_A, start_B), now] for
    a fair same-regime comparison; eligibility + the dead-agent rule keep it >= 60 days.
  * Each matchup score S = G * M on the overlap, plus a LONGEVITY BONUS crediting the
    older miner's pre-overlap record (decayed, upside-only) onto R and X -- never the
    penalties.
  * Winner accrues (S_winner - S_loser), loser 0; margins sum into one UNIFIED score.

From unified scores to weights:
  * Emissions split into a human pool and an agent pool, each distributed among its
    own members by the unified score.
  * The human pool is sized in DOLLAR terms (agent-independent), agents absorb the rest:
        human_share = min(HUMAN_SHARE_CAP, HUMAN_PNL_K * human_PnL$ / emission_USD)
        agent_share = 1 - human_share
    What humans don't take goes to agents (or burns if none).

Agent-lane open-source phases (emission is alive from day one):
  * GRACE -- before any agent open-sources. Closed-source agents earn with NO eligibility
    gate (just survive elimination + be measurable); the concentration penalty K stops a
    one-lucky-day account running away.
  * OPEN -- once any agent open-sources (honored only at >= 60d runtime), the lane LATCHES:
    only open-source agents earn, under the full 60d eligibility.
  * Humans are never eligibility-gated (measurable + not eliminated, bounded by the cap).

Longevity bonus (locked design parameters KAPPA=0.5, H=90): each pre-overlap day d (<= the
overlap start t) weights its daily % (R) and daily $ (X) by w(d) = 2^(-(t - d)/H), fading
backward from t. bonus = KAPPA * max(0, sum) -- upside-only. A 90-day record ~= half a 60-day
live window; a lifelong record caps at ~one window (favored, never dominating).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from .config import HUMAN_PNL_K, HUMAN_SHARE_CAP, PNL_WINDOW_DAYS
from .scoring import (
    REQUIRED_RUNTIME_DAYS,
    DailyRecord,
    check_eligibility,
    check_elimination,
    compute_metrics,
    compute_punishment,
    compute_utility,
    daily_pct,
)

# Longevity-bonus parameters (locked design; see module docstring).
H_DECAY = 90.0     # half-life in days
KAPPA = 0.5        # bonus weight


@dataclass
class MinerData:
    """One miner's full input for the tournament.

    records is the complete lifetime daily series (one DailyRecord per UTC day).
    observations is every coin traded, for the trading-restriction elimination rule;
    None skips that rule. gap_dates are genuine data-outage days. open_sourced is
    honored only at >= 60d runtime; once ANY agent's claim is honored the lane latches
    to "only open-source agents earn" (see tournament_weights); ignored for humans.
    """

    address: str
    records: list[DailyRecord]
    account_type: str = "agent"
    observations: list[str] | None = None
    gap_dates: set[dt.date] = field(default_factory=set)
    open_sourced: bool = False

    @property
    def start(self) -> dt.date | None:
        return self.records[0].date if self.records else None

    @property
    def end(self) -> dt.date | None:
        return self.records[-1].date if self.records else None


# ---------------------------------------------------------------------------
# Longevity bonus
# ---------------------------------------------------------------------------

def _daily_dollars(records: list[DailyRecord]) -> list[tuple[dt.date, float]]:
    """Flow-adjusted $ gain per day: g(d) = equity(d) - equity(d-1) - net_flow(d),
    the real money made trading that day with flows removed."""
    out: list[tuple[dt.date, float]] = []
    prev: DailyRecord | None = None
    for r in records:
        if prev is None:
            prev = r
            continue
        out.append((r.date, r.equity - prev.equity - r.net_flow))
        prev = r
    return out


def longevity_bonus(
    pct_series: list[tuple[dt.date, float]],
    dollar_series: list[tuple[dt.date, float]],
    overlap_start: dt.date,
    kappa: float = KAPPA,
    H: float = H_DECAY,
) -> tuple[float, float]:
    """(bonus_R, bonus_X): decayed, upside-only credit for pre-overlap history.

    Days on/before ``overlap_start`` contribute their daily % (-> R) and daily $ (-> X)
    weighted 2^(-(overlap_start - d)/H), fading backward from the overlap start. Both
    bonuses are >= 0 (a net-negative past adds nothing). The series are precomputed once."""
    bR = sum(
        p * 2.0 ** (-(overlap_start - d).days / H)
        for d, p in pct_series if d <= overlap_start
    )
    bX = sum(
        g * 2.0 ** (-(overlap_start - d).days / H)
        for d, g in dollar_series if d <= overlap_start
    )
    return kappa * max(0.0, bR), kappa * max(0.0, bX)


# ---------------------------------------------------------------------------
# Matchup score (one miner, on one overlap window, with the longevity bonus)
# ---------------------------------------------------------------------------

def matchup_score(
    miner: MinerData,
    overlap_start: dt.date,
    end_date: dt.date,
    kappa: float = KAPPA,
    H: float = H_DECAY,
    series: tuple[list, list] | None = None,
) -> float:
    """S = G * M for ``miner`` on the overlap window [overlap_start, end_date].

    The reward terms R and X are boosted by the longevity bonus, but the penalties
    (D, V, K) and the punishment multiplier M are computed on the OVERLAP ONLY -- the
    long record earns reward credit but never buys back risk taken against this opponent.
    ``series`` may carry the precomputed (pct_series, dollar_series)."""
    window = [r for r in miner.records if overlap_start <= r.date <= end_date]
    if len(window) < 2:
        return 0.0
    m = compute_metrics(window, end_date, miner.gap_dates)
    if series is None:
        series = (daily_pct(miner.records, miner.gap_dates), _daily_dollars(miner.records))
    bonus_R, bonus_X = longevity_bonus(series[0], series[1], overlap_start, kappa, H)
    util = compute_utility(m.R + bonus_R, m.X + bonus_X, m.D, m.V, m.K, m.conc_ramp_days)
    pun = compute_punishment(m.rolling_30d_pnl, m.Q_EV)
    s = util.performance_score_G * pun.M_total
    return s if math.isfinite(s) and s > 0.0 else 0.0


# ---------------------------------------------------------------------------
# Eligibility gate (lifetime) + the pairwise tournament
# ---------------------------------------------------------------------------

def is_in_tournament(
    miner: MinerData, end_date: dt.date, ever_open_sourced: bool = False
) -> tuple[bool, list[str]]:
    """Lifetime gate: a miner enters iff NOT eliminated AND eligible, both judged on the
    full history up to end_date (not the overlap). Returns (ok, reasons).

    GRACE (humans always; agents while ``ever_open_sourced`` is unset): no eligibility
    gate, just survive elimination and be MEASURABLE (>= 1 traded day). OPEN (agents once
    the latch is set): the full eligibility gate, and only agents whose open-source claim
    is honored (open_sourced AND >= 60d) earn -- closed-source or under-age agents drop."""
    records = [r for r in miner.records if r.date <= end_date]  # no future leakage
    if not records:
        return False, ["no data"]
    elim = check_elimination(records, end_date, miner.observations, miner.gap_dates)
    if elim.eliminated:
        return False, elim.reasons
    m = compute_metrics(records, end_date, miner.gap_dates)
    is_agent = miner.account_type != "human"

    if is_agent and ever_open_sourced and not (
        miner.open_sourced and m.runtime_days >= REQUIRED_RUNTIME_DAYS
    ):
        return False, ["closed-source agent in the open-source phase"]

    grace = (not is_agent) or (not ever_open_sourced)
    if grace:
        # No eligibility thresholds, only "measurable": >= 1 traded day to score.
        if len(records) < 2 or m.active_days < 1:
            return False, ["not yet measurable (need >= 1 traded day)"]
        return True, []

    failures = check_eligibility(miner.account_type, m)
    return (not failures), failures


def run_tournament(
    miners: list[MinerData],
    end_date: dt.date,
    kappa: float = KAPPA,
    H: float = H_DECAY,
) -> dict[str, float]:
    """Play every pair among the given (already-eligible) miners; return
    {address: accumulated winning margin}. Winner of each pair accrues
    (S_win - S_lose); loser 0."""
    # Precompute each miner's series once and memoize each (miner, overlap_start) score;
    # opponents sharing a start date reuse it.
    series = {
        mn.address: (daily_pct(mn.records, mn.gap_dates), _daily_dollars(mn.records))
        for mn in miners
    }
    cache: dict[tuple[str, dt.date], float] = {}

    def score(mn: MinerData, t: dt.date) -> float:
        key = (mn.address, t)
        if key not in cache:
            cache[key] = matchup_score(mn, t, end_date, kappa, H, series[mn.address])
        return cache[key]

    scores = {mn.address: 0.0 for mn in miners}
    for i in range(len(miners)):
        for j in range(i + 1, len(miners)):
            a, b = miners[i], miners[j]
            t = max(a.start, b.start)  # overlap start = the later first-activity day
            if t >= end_date:
                continue
            sa, sb = score(a, t), score(b, t)
            if sa > sb:
                scores[a.address] += sa - sb
            elif sb > sa:
                scores[b.address] += sb - sa
    return scores


def _window_dollars(
    records: list[DailyRecord], end_date: dt.date, window_days: int
) -> float:
    """Sum of flow-adjusted daily-$ over the trailing ``window_days`` ending at
    ``end_date``. NET (losing days subtract), so a churning or fed account does not
    look productive."""
    cut = end_date - dt.timedelta(days=window_days)
    return sum(g for d, g in _daily_dollars(records) if cut < d <= end_date)


def tournament_weights(
    miners: list[MinerData],
    emission_usd: float | None,
    ever_open_sourced: bool = False,
    end_date: dt.date | None = None,
    kappa: float = KAPPA,
    H: float = H_DECAY,
    human_cap: float = HUMAN_SHARE_CAP,
    k: float = HUMAN_PNL_K,
    window_days: int = PNL_WINDOW_DAYS,
) -> dict[str, float]:
    """Full pipeline: lifetime gate -> ONE unified tournament -> capped weights.

    Returns {address: weight} over ALL input miners (ineligible/eliminated and non-winners
    get 0). Weights sum to <= 1; the gap (no eligible agents, no eligible humans, missing
    emission_usd, ...) is left for the caller to BURN.

    ``emission_usd`` is the USD value of one round's emission (pricing.py). None or
    non-positive -> human cap is 0 (humans earn nothing, agents absorb), so a missing
    price feed degrades safely instead of crashing.

    ``ever_open_sourced`` is the persistent open-source latch (durable on-chain state),
    OR-ed with "any agent open-sourced this round (>= 60d)" so the round latches the moment
    the first qualifying open-source agent appears even before the durable flag is wired."""
    if end_date is None:
        ends = [mn.end for mn in miners if mn.end is not None]
        end_date = max(ends) if ends else None
    out = {mn.address: 0.0 for mn in miners}
    if end_date is None:
        return out

    # One competitor per address (a duplicate HL account is the SAME account and must not
    # play twice), and drop records after end_date so an explicit snapshot can't leak
    # future data into the lifetime gate (matching evaluate()'s contract).
    by_addr: dict[str, MinerData] = {}
    for mn in miners:
        recs = [r for r in mn.records if r.date <= end_date]
        by_addr[mn.address] = MinerData(
            mn.address, recs, mn.account_type, mn.observations, mn.gap_dates,
            mn.open_sourced,
        )

    # The latch: durable flag OR any agent open-sourced AND >= 60d this round.
    def _runtime(mn: MinerData) -> int:
        return (end_date - mn.records[0].date).days if mn.records else 0

    latched = ever_open_sourced or any(
        mn.open_sourced and _runtime(mn) >= REQUIRED_RUNTIME_DAYS
        for mn in by_addr.values()
        if mn.account_type != "human"
    )

    eligible = [mn for mn in by_addr.values() if is_in_tournament(mn, end_date, latched)[0]]
    scores = run_tournament(eligible, end_date, kappa, H)  # unified, cross-type included

    humans = [mn for mn in eligible if mn.account_type == "human"]
    agents = [mn for mn in eligible if mn.account_type != "human"]
    human_score = sum(scores.get(mn.address, 0.0) for mn in humans)
    agent_score = sum(scores.get(mn.address, 0.0) for mn in agents)

    # Human pool size: dollar-tethered (agent-independent), capped. PnL is compared on the
    # SAME horizon as the emission (window_days=1 -> that day's PnL vs that day's emission);
    # a net-losing human earns nothing.
    pnl_h = sum(max(0.0, _window_dollars(mn.records, end_date, window_days)) for mn in humans)
    pnl_h = pnl_h / window_days if window_days > 0 else 0.0
    if (
        human_score > 0.0
        and pnl_h > 0.0
        and emission_usd is not None
        and math.isfinite(emission_usd)
        and emission_usd > 0.0
    ):
        human_share = min(human_cap, k * pnl_h / emission_usd)
    else:
        human_share = 0.0
    if not math.isfinite(human_share) or human_share < 0.0:
        human_share = 0.0
    agent_share = 1.0 - human_share

    # Split each pool among its own members by unified score. A pool with no eligible
    # members (or all-zero score) is left unassigned -- that share becomes the caller's burn.
    if human_score > 0.0:
        for mn in humans:
            s = scores.get(mn.address, 0.0)
            if s > 0.0:
                out[mn.address] = human_share * s / human_score
    if agent_score > 0.0:
        for mn in agents:
            s = scores.get(mn.address, 0.0)
            if s > 0.0:
                out[mn.address] = agent_share * s / agent_score
    return out
