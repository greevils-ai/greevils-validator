"""Pure scoring math for the Greevils arena (ported from the incmec evaluator).

Deterministic, network-free core of the per-account evaluation. Operates on a
per-trader DAILY series -- one record per UTC day (flow-adjusted end-of-day
balance, net flow, executed volume; a day is "active" iff volume > 0), supplied
by the validator's daily DB (db.py). Pipeline per account: ELIMINATION ->
ELIGIBILITY -> UTILITY -> PUNISHMENT -> countable_score.

GAP-AWARE (key invariant): a Reservoir publish gap (e.g. the Oct-Dec window) is
a *data* hole, not inactivity -- windows with no covered day are SKIPPED, never
eliminated, so a gap can't falsely kill an account. Constants kept identical to
incmec/scoring.py.

All percentages are decimals (10% == 0.10). All days are UTC.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Model constants (identical to incmec/scoring.py)
# ---------------------------------------------------------------------------

# Elimination thresholds
# Drawdown is deliberately NOT an elimination trigger nor a punishment multiplier:
# it is scored by the single -LAMBDA*(D/D0)^2 utility term, which already drives a
# catastrophic drawdown's score to ~0. The old hard cap and M_D multiplier were
# removed as double-counts of this same metric D.
EQUITY_FLOOR = 1000.0              # trading trough below $1000 -> eliminated
GRACE_PERIOD_DAYS = 7              # dead-agent rule grace period after first activity
DEAD_AGENT_WINDOW_DAYS = 14        # dead iff this many consecutive days with ZERO trading
# No volume floor in the dead-agent rule -- a thin-but-alive account is handled by
# the soft M_EV penalty (Q_EV < 4 -> 0.5), not elimination.

# Trading-restriction whitelist -- the DEFINITIVE set of tradable pairs. ONLY these are allowed;
# trading anything else (or any spot) is an INSTANT, permanent elimination (even a single trade,
# no leverage cap). Keys are normalized (see normalize_pair), which strips the HIP-3 dex prefix
# (xyz:NVDA -> NVDA) so a symbol matches the asset on any dex. The non-perp names (stocks / index /
# commodity / forex) live on HIP-3 dexes; each symbol MUST match HL's real coin string exactly --
# validator.py warns at startup about any that match no live HL market (a typo would false-DQ).
ALLOWED_PAIRS: set[str] = {
    # Perps (native Hyperliquid)
    "BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "HYPE", "DOGE", "ZEC", "XLM", "ADA",
    "LINK", "BCH", "HBAR", "LTC", "SUI", "AVAX", "NEAR", "TAO", "WLFI",
    "PAXG", "MNT", "ONDO", "ASTER", "WLD", "DOT", "UNI", "ICP",
    # Indices
    "SP500", "XYZ100",
    # Stocks
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "TSLA", "META",
    "MU", "AMD", "INTC", "SNDK", "MSTR",
    # Commodities
    "GOLD", "SILVER", "BRENTOIL",
    # Forex
    "EUR", "JPY",
}

# Eligibility thresholds (fixed lifetime requirements). The OPEN-phase gate --
# applied only to open-source agents once the lane has latched (see
# tournament.is_in_tournament); GRACE-phase agents and humans are never eligibility-
# gated, only needing to survive elimination and be measurable (>= 1 traded day).
# REQUIRED_RUNTIME_DAYS also doubles as the minimum age at which an open-source claim is honored.
RETURN_HURDLE = 0.015             # 1.5% return-on-average-capital PnL hurdle
REQUIRED_RUNTIME_DAYS = 60
REQUIRED_ACTIVE_DAYS = 40

# Utility constants (spec)
BETA = 0.08        # weight on the absolute-PnL reward term BETA*ln(1+X/S). Anchored so
                   # $100k of absolute profit ~= a +45% return: (1+100000/S)^BETA - 1 ~= 0.45.
                   # Log-saturating, so size credit compresses: $10k~=21%, $500k~=65%, $1M~=74%.
S = 1000.0         # scale (USD) inside the absolute-PnL log term
LAMBDA = 0.308     # weight on the drawdown penalty term (~= ln(2)/2.25): a D=15% max
                   # drawdown ~= offsets a 2x (R=100%) return. ln(1+R)=lambda*(0.15/D0)^2.
D0 = 0.10          # drawdown reference scale (10%)
MU = 0.15          # weight on the downside-volatility penalty. Anchored so a 5% daily downside-vol
                   # ~= a 25% return penalty (25.2%; mu ~= LAMBDA/2 at the unit point). Junior to
                   # drawdown (5% vol's 25% < a 10% drawdown's 36%), but a real secondary risk gate.
V0 = 0.02          # downside-volatility threshold (2%): below this the penalty ~= 0 (normal traders
                   # sit at V~0.5-1%); above it the softplus hinge engages.
K_SOFTPLUS = 6     # softplus sharpness for the downside-vol term (smooth elbow at V0)
ETA = 0.75         # weight on concentration penalty = -ETA*max(0, K-K0). Anchored so all profit in
                   # ONE lucky day (K=1) ~= a 90% return penalty (e^(0.75*0.86)-1 ~= 90%). A pure
                   # luck/repeatability gate -- dormant for 7+ profit-day accounts, bites the day-wonders.
K0 = 0.14          # concentration threshold = 1/7.1: no penalty if profit is spread over ~7+ effective
                   # days (K = 1/effective-profit-days); below that the penalty grows.
CONC_RAMP_DAYS = 14  # concentration penalty ramps to full over this many NON-PROFIT days, scaled by
                     # min(1, (1+non_profit_days)/CONC_RAMP_DAYS): the ramp advances ONLY on days that
                     # added no profit (flat/down), so a run of profitable days freezes it at the day-1
                     # level (~1/14) while a one-day-wonder that then goes idle still reaches full by ~14 days.
ALPHA = 1.5        # performance-transform curvature = exponent on the money-multiple. Since
                   # G ∝ (1+R)^ALPHA - 1, each doubling of a trader's book multiplies its PvP score by
                   # 2^ALPHA (~2.83x at 1.5). Kept <=~2 so a lone 10x outlier can't bulldoze the tournament.

# Punishment constants (behavioral-discipline haircuts; drawdown is NOT here --
# it is scored entirely by the -LAMBDA*(D/D0)^2 utility term)
P30_PENALTY = 0.50           # 50% haircut when rolling-30d PnL < 0 (same severity as M_EV)
EV_PENALTY = 0.50            # 50% haircut when Q_EV < 4
Q_EV_THRESHOLD = 4.0         # Q_EV >= 4 -> no executed-value punishment. EV counts BOTH sides
                             # (entries+exits), so 4x avg capital over 14d ~= two round-trips (~weekly).

# Return-percentage denominator floor: the daily return % divides by AT LEAST this much capital, so a
# small account's big-percentage swings scale to a realistic size (a $300 gain on $1000 reads as +3%,
# not +30%). Removes the small-account %-inflation edge -- with NO hard elimination (see EQUITY_FLOOR).
RETURN_DENOM_FLOOR = 10000.0


# ---------------------------------------------------------------------------
# Daily data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyRecord:
    """One UTC day of a single account.

    equity is the end-of-day mark-to-market account value; net_flow is deposits
    minus withdrawals attributed to this day (perp scope), used to flow-adjust so
    capital movements aren't mistaken for PnL; volume is executed value (sum |px*sz|).
    A day is "active" iff volume > 0 -- derived, not stored (a fill always has px*sz > 0).
    """

    date: dt.date
    equity: float
    net_flow: float = 0.0
    volume: float = 0.0


# ---------------------------------------------------------------------------
# Flow-adjusted drawdown
# ---------------------------------------------------------------------------

def _step_clean(prev_date: dt.date, cur_date: dt.date, gap_dates: set[dt.date]) -> bool:
    """A day-over-day step counts only when the two days are ADJACENT and neither is
    a Reservoir outage. A step spanning a gap covers unknown data, so it is carried
    flat -- gap-awareness applied to drawdown/return/vol so an outage can never book
    a phantom move (or hide a real one)."""
    if (cur_date - prev_date).days != 1:
        return False
    return prev_date not in gap_dates and cur_date not in gap_dates


def daily_pct(
    records: list[DailyRecord], gap_dates: set[dt.date] | None = None
) -> list[tuple[dt.date, float]]:
    """THE single daily-PnL% series (the cross-cutting ground truth).

    One entry per day after the first record (the baseline), each the flow-punishing
    ``net_pnl_pct(start, end, net_flow)``. Carried flat (0%) when the prior day's
    capital is below the equity floor (near-zero base is meaningless) or the step
    spans a gap/outage (see _step_clean). EVERYTHING except X (real absolute PnL) --
    R, D, V, K, the rolling-30d figure -- derives from this one series.
    """
    gap_dates = gap_dates or set()
    out: list[tuple[dt.date, float]] = []
    prev: DailyRecord | None = None
    for r in records:
        if prev is None:
            prev = r
            continue
        if prev.equity < EQUITY_FLOOR or not _step_clean(prev.date, r.date, gap_dates):
            out.append((r.date, 0.0))
        else:
            out.append((r.date, net_pnl_pct(prev.equity, r.equity, r.net_flow)))
        prev = r
    return out


def _nav_curve(pct: list[tuple[dt.date, float]]) -> tuple[float, float]:
    """Compound the daily-% series into a NAV and return (R, D):
    R = final NAV - 1 (the compounded return), D = max peak-to-trough drawdown.
    NAV is clamped at 0 each step so a worse-than -100% day caps it (D in [0,1])."""
    nav = 1.0
    peak = 1.0
    mdd = 0.0
    for _, p in pct:
        nav *= max(0.0, 1.0 + p)
        if nav > peak:
            peak = nav
        if peak > 0.0:
            mdd = max(mdd, (peak - nav) / peak)
    return nav - 1.0, mdd


def max_drawdown(records: list[DailyRecord], gap_dates: set[dt.date] | None = None) -> float:
    """Max peak-to-trough drawdown of the daily-PnL% NAV (flow-punishing series, so a
    sus flow registers as drawdown while a pure deposit stays at 0% / no fake drawdown)."""
    return _nav_curve(daily_pct(records, gap_dates))[1]


def peak_equity(records: list[DailyRecord]) -> float:
    """Most capital ever deployed (max raw end-of-day equity)."""
    return max((r.equity for r in records), default=0.0)


# ---------------------------------------------------------------------------
# Dead-agent / inactivity rule
# ---------------------------------------------------------------------------

def check_dead_agent(
    records: list[DailyRecord],
    end_date: dt.date,
    gap_dates: set[dt.date] | None = None,
) -> list[str]:
    """Rolling 14-day TOTAL-inactivity rule on a continuous daily grid, gap-aware.

    After the first ``GRACE_PERIOD_DAYS`` of activity, eliminate if ANY rolling
    14-day window (last ending exactly at ``end_date``) has ZERO active days
    (abandonment). Trading even once in a window keeps it alive; a thin-but-alive
    account is disciplined by the soft M_EV penalty instead.

    The grid is continuous from the first record to ``end_date``, treating days with
    no record as inactive -- so an account that simply STOPS producing records is
    correctly judged abandoned rather than silently surviving on missing data.
    GAP-AWARENESS: only ``gap_dates`` (genuine Reservoir outages, activity unknown)
    are data holes; any window overlapping one is skipped, never eliminated.
    """
    if not records:
        return []
    gap_dates = gap_dates or set()
    active_by_date = {r.date: r.volume > 0.0 for r in records}  # active <=> traded (volume>0)
    one_day = dt.timedelta(days=1)
    first_day = records[0].date

    # Continuous grid [first_day .. end_date]: (active, is_gap); no record -> inactive.
    grid: dict[dt.date, tuple[bool, bool]] = {}
    d = first_day
    while d <= end_date:
        grid[d] = (active_by_date.get(d, False), d in gap_dates)
        d += one_day

    ws = first_day + dt.timedelta(days=GRACE_PERIOD_DAYS)
    # Last window start so the window's final day is exactly end_date.
    last_ws = end_date - dt.timedelta(days=DEAD_AGENT_WINDOW_DAYS - 1)

    cur = ws
    while cur <= max(ws, last_ws):
        window_dates = [cur + dt.timedelta(days=i) for i in range(DEAD_AGENT_WINDOW_DAYS)]
        if window_dates[-1] <= end_date and not any(grid[d][1] for d in window_dates):
            if not any(grid[d][0] for d in window_dates):
                return [
                    f"dead-agent: no trading for the {DEAD_AGENT_WINDOW_DAYS}-day "
                    f"window starting {cur} (UTC) -- account inactive/abandoned"
                ]
        cur += one_day

    return []


# ---------------------------------------------------------------------------
# Trading-restriction rule (allowed pairs + max leverage) -- instant DQ
# ---------------------------------------------------------------------------

def normalize_pair(coin: str) -> str:
    """Map a Hyperliquid coin symbol to an ALLOWED_PAIRS key.

    Drops a HIP-3 dex prefix ("xyz:NVDA" -> "NVDA"), uppercases, and strips
    non-alphanumerics ("S&P500" -> "SP500"). Does NOT strip a leading 'k' (kPEPE
    is a distinct asset, not PEPE). Aliases for symbols whose on-chain string
    differs from the spec name belong here once confirmed against live data.
    """
    s = coin.split(":")[-1]
    return "".join(ch for ch in s.upper() if ch.isalnum())


def is_spot_symbol(coin: str) -> bool:
    """True for a Hyperliquid SPOT market.

    Spot uses '@<index>' (e.g. '@156') or 'BASE/QUOTE' (e.g. 'PURR/USDC'); perps
    are plain tickers ('BTC') or 'dex:SYMBOL' ('xyz:NVDA'). Spot trading is not
    part of this competition -- any spot fill is an instant disqualification.
    """
    return coin.startswith("@") or "/" in coin


def check_trading_restrictions(coins: list[str]) -> list[str]:
    """Eliminate on any spot trade or any off-whitelist pair (no leverage check).

    A single violating trade disqualifies. ``coins`` is every market the account
    traded. Returns one reason per distinct violating market (empty == clean).
    """
    reasons: list[str] = []
    spot_seen: set[str] = set()
    bad_pair: set[str] = set()
    for coin in coins:
        # Spot checked before normalization so it can't be confused with a perp ticker.
        if is_spot_symbol(coin):
            if coin not in spot_seen:
                spot_seen.add(coin)
                reasons.append(f"traded spot market {coin!r} (spot not allowed)")
            continue
        key = normalize_pair(coin)
        if key not in ALLOWED_PAIRS and key not in bad_pair:
            bad_pair.add(key)
            reasons.append(f"traded disallowed pair {coin!r}")
    return reasons


# ---------------------------------------------------------------------------
# Elimination assembly
# ---------------------------------------------------------------------------

@dataclass
class EliminationResult:
    eliminated: bool
    reasons: list[str] = field(default_factory=list)
    max_drawdown_D: float = 0.0     # informational only (drawdown is scored by the soft penalties, not eliminated)
    peak_equity: float = 0.0


def check_elimination(
    records: list[DailyRecord],
    end_date: dt.date,
    coins: list[str] | None = None,
    gap_dates: set[dt.date] | None = None,
) -> EliminationResult:
    """Run all elimination rules on the daily series and aggregate reasons.

    Rule 0 -- trading restriction (instant DQ on spot or off-whitelist pair; skipped
              if ``coins`` not provided).
    Rule 1 -- dead-agent / inactivity (continuous-grid, gap-aware via ``gap_dates``).
    Rule 2 -- equity floor: RAW end-of-day balance < $1000 on ANY day -> eliminated
              (a withdrawal below the floor counts; must keep >= $1000 at all times).

    Drawdown is intentionally NOT an elimination rule (scored by the -LAMBDA*(D/D0)^2
    utility term); D and peak are computed only for the informational result fields.
    """
    reasons: list[str] = []

    if coins:
        reasons.extend(check_trading_restrictions(coins))

    # Informational only (drawdown is scored by the utility term, not eliminated).
    D = max_drawdown(records, gap_dates)
    peak = peak_equity(records)

    reasons.extend(check_dead_agent(records, end_date, gap_dates))

    min_rec = min(records, key=lambda r: r.equity)
    if min_rec.equity < EQUITY_FLOOR:
        reasons.append(
            f"equity floor: balance {min_rec.equity:.2f} < {EQUITY_FLOOR:.0f} "
            f"on {min_rec.date} (UTC)"
        )

    return EliminationResult(
        eliminated=bool(reasons),
        reasons=reasons,
        max_drawdown_D=D,
        peak_equity=peak,
    )


# ---------------------------------------------------------------------------
# Lifetime metrics derived from the daily series
# ---------------------------------------------------------------------------

def _day_ms(d: dt.date) -> int:
    """Epoch ms at 00:00:00 UTC of a date (for time-weighting)."""
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def net_pnl_pct(start_equity: float, end_equity: float, net_flow: float) -> float:
    """Flow-punishing daily return % (the agreed anti-gaming rule).

        S = start equity, E = end equity, N = net flow (deposits - withdrawals)
        P = E - S - N  (trading PnL, flows removed);  F = RETURN_DENOM_FLOOR

        P > 0:  N > 0 -> P / max(S+N, F)     N <= 0 -> (P + N) / max(S, F)   # PROFIT: denom floored at F
        P <= 0: N > 0 -> (P/S)·(1 + N/(S+N)) N <= 0 -> P / (S + N)           # LOSS: real denom (full loss)

    The denominator floor applies to PROFIT days ONLY: a small account's GAINS scale to a realistic
    size (a $300 gain on a $1000 account reads as +3%, not +30%), while its LOSSES still hit in FULL
    (a $300 loss on $1000 is -30%) -- no upside %-inflation edge, and no downside leniency.
    Loss+deposit scales the LOSS RATE up by the fresh-deposit fraction (factor
    1..2) rather than counting the whole deposit as a loss -- a softer anti-rescue
    penalty. KEY INVARIANT: within each branch ANY flow can only LOWER this value,
    never raise it (brute-force verified), so it punishes
    exploit-like flows (withdraw-after-profit, deposit-after-loss) and never rewards
    them. The lifetime R compounds these daily %s into a NAV, staying monotone in
    them. Returns a fraction (0.10 == +10%).
    """
    S, N = start_equity, net_flow
    if S <= 0:
        return 0.0
    P = end_equity - S - N
    if P > 0:                                # PROFIT day: denominator floored at RETURN_DENOM_FLOOR
        F = RETURN_DENOM_FLOOR
        return P / max(S + N, F) if N > 0 else (P + N) / max(S, F)
    if N > 0:                                # LOSS + deposit: REAL denominator (no floor -- losses hit in full)
        return (P / S) * (1.0 + N / (S + N))
    d = S + N                                # LOSS: REAL denominator (no floor)
    return P / d if d > 0 else -1.0


def _real_absolute_pnl(records: list[DailyRecord]) -> float:
    """Real lifetime trading PnL in dollars -- NEUTRAL (flows removed, NOT punished):
    final_equity - opening_equity - flows_after_day_0. The ONLY input not derived
    from the punished daily-% series; feeds X (absolute-PnL reward) and the PnL hurdle."""
    if len(records) < 2:
        return 0.0
    return records[-1].equity - records[0].equity - sum(r.net_flow for r in records[1:])


def _time_weighted_avg_equity(records: list[DailyRecord]) -> float:
    """Time-weighted average of raw end-of-day equity (trapezoidal over dates).

    Uses raw equity (mark-to-market account value, including deposited capital),
    matching incmec's A = time-weighted average capital deployed.
    """
    if not records:
        return 0.0
    if len(records) == 1:
        return records[0].equity
    area = 0.0
    span = 0.0
    for a, b in zip(records, records[1:]):
        dt_ms = _day_ms(b.date) - _day_ms(a.date)
        area += (a.equity + b.equity) / 2.0 * dt_ms
        span += dt_ms
    return area / span if span > 0 else records[-1].equity


def _concentration_K(values) -> float:
    """Profit concentration K = Herfindahl of the positive daily PnL%s. ~0 means
    profit was spread over many days; ~1 means one day made (almost) all of it.
    No positive days -> K = 1 (maximally concentrated / no profit)."""
    pos = [v for v in values if v > 0]
    gross = sum(pos)
    if gross <= 0.0:
        return 1.0
    return sum((v / gross) ** 2 for v in pos)


@dataclass
class Metrics:
    A: float                    # time-weighted average capital
    net_pnl: float              # REAL absolute trading PnL ($), neutral (-> X, hurdle)
    R: float                    # compounded return of the punished daily-% series
    pct_sum: float              # SUM of the daily %s -> the 1.5% PnL eligibility hurdle
    X: float                    # excess absolute PnL = max(0, real net_pnl)
    D: float                    # max drawdown of the daily-% NAV (punished)
    V: float                    # downside volatility of the daily-% series (punished)
    K: float                    # concentration of the positive daily %s (punished)
    runtime_days: float
    active_days: int
    total_executed_value: float
    rolling_30d_pnl: float       # sum of the last-30-day daily %s (punished) -> M_P
    Q_EV: float                 # trailing-14d executed value / avg capital
    peak_equity: float
    scored_days: int = 0        # number of daily-% observations in this window (informational)
    conc_ramp_days: int = 1     # 1 + non-profit days; scales the concentration penalty by
                                # min(1, conc_ramp_days/CONC_RAMP_DAYS) -- climbs only on no-profit days


def compute_metrics(
    records: list[DailyRecord], end_date: dt.date, gap_dates: set[dt.date] | None = None
) -> Metrics:
    """Derive all lifetime scoring inputs from the daily series."""
    if not records:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 0, 0)

    gap_dates = gap_dates or set()
    first_date = records[0].date
    runtime_days = (end_date - first_date).days

    # THE single ground-truth series; R and D come from the SAME NAV of it.
    pct = daily_pct(records, gap_dates)
    R, D = _nav_curve(pct)
    pct_sum = sum(p for _, p in pct)  # simple SUM of daily %s -> the 1.5% hurdle

    # Downside volatility: sqrt(mean of squared negative daily %s). The baseline day
    # contributes a 0 but is counted in the denominator -- matching incmec's every-day count.
    sq_down = sum(min(0.0, p) ** 2 for _, p in pct)
    n_ret = len(pct) + (1 if records[0].equity > 0.0 else 0)
    V = math.sqrt(sq_down / n_ret) if n_ret else 0.0

    K = _concentration_K(p for _, p in pct)
    nonprofit_days = sum(1 for _, p in pct if p <= 0.0)

    # X / eligibility basis: REAL absolute trading PnL in $ (neutral, not punished).
    net_pnl = _real_absolute_pnl(records)
    X = max(0.0, net_pnl)

    A = _time_weighted_avg_equity(records)
    active_days = sum(1 for r in records if r.volume > 0.0)
    total_ev = sum(r.volume for r in records)

    # Rolling window closed on the right at end_date: sum of the last-30-day %s.
    cut30 = end_date - dt.timedelta(days=30)
    rolling_30d = sum(p for d, p in pct if cut30 < d <= end_date)

    cut14 = end_date - dt.timedelta(days=14)
    recent = [r for r in records if cut14 < r.date <= end_date]
    ev14 = sum(r.volume for r in recent)
    a14 = _time_weighted_avg_equity(recent) if recent else 0.0  # time-weighted, not mean
    Q_EV = ev14 / a14 if a14 > 0 else 0.0

    return Metrics(
        A=A, net_pnl=net_pnl, R=R, pct_sum=pct_sum, X=X, D=D, V=V, K=K,
        runtime_days=runtime_days, active_days=active_days,
        total_executed_value=total_ev, rolling_30d_pnl=rolling_30d,
        Q_EV=Q_EV, peak_equity=peak_equity(records), scored_days=len(pct),
        conc_ramp_days=1 + nonprofit_days,
    )


# ---------------------------------------------------------------------------
# Eligibility (1.5% PnL hurdle, runtime/active-days/executed-value gates)
# ---------------------------------------------------------------------------

def _executed_value_hurdle(account_type: str, A: float) -> float:
    """human: EV >= max(500000, 25*A); agent: EV >= 25*A."""
    base = 25.0 * A
    return max(500_000.0, base) if account_type == "human" else base


def check_eligibility(account_type: str, m: Metrics) -> list[str]:
    """Return eligibility-failure reasons (empty list == eligible). The FULL OPEN-phase
    gate -- applied only to open-source agents once the lane has latched (grace-phase
    agents and humans are not gated; see tournament.is_in_tournament).

    PnL hurdle, two independent parts: the 1.5% RETURN hurdle on the daily-% sum
    (m.pct_sum, a real percent -- not 1.5%*A in dollars), and humans ALSO need real
    absolute profit >= $500 (pure X, unpunished dollars).
    """
    failures: list[str] = []
    if m.runtime_days < REQUIRED_RUNTIME_DAYS:
        failures.append(f"runtime_days {m.runtime_days:.0f} < required {REQUIRED_RUNTIME_DAYS}")
    if m.active_days < REQUIRED_ACTIVE_DAYS:
        failures.append(f"active_days {m.active_days} < required {REQUIRED_ACTIVE_DAYS}")
    if m.pct_sum < RETURN_HURDLE:
        failures.append(f"return {m.pct_sum:.4f} < required {RETURN_HURDLE:.3f} (1.5%)")
    if account_type == "human" and m.X < 500.0:
        failures.append(f"real profit X {m.X:.2f} < required 500")
    ev_hurdle = _executed_value_hurdle(account_type, m.A)
    if m.total_executed_value < ev_hurdle:
        failures.append(
            f"total_executed_value {m.total_executed_value:.2f} < hurdle {ev_hurdle:.2f}"
        )
    return failures


# ---------------------------------------------------------------------------
# Utility U, performance transform G
# ---------------------------------------------------------------------------

def softplus(x: float, k: float = K_SOFTPLUS) -> float:
    """Numerically stable softplus: ln(1 + exp(k*x)) / k."""
    z = k * x
    return (max(0.0, z) + math.log1p(math.exp(-abs(z)))) / k


@dataclass
class UtilityTerms:
    return_term: float
    absolute_pnl_term: float
    drawdown_term: float
    downside_volatility_term: float
    concentration_term: float
    utility_U: float
    U_plus: float
    performance_score_G: float


def performance_transform(U_plus: float) -> float:
    """G = (exp(ALPHA*U_plus) - 1) / (exp(ALPHA) - 1). G(0)=0, G(1)=1."""
    return (math.exp(ALPHA * U_plus) - 1.0) / (math.exp(ALPHA) - 1.0)


def compute_utility(
    R: float, X: float, D: float, V: float, K: float, conc_ramp_days: int | None = None
) -> UtilityTerms:
    """U = ln(1+R) + BETA*ln(1+X/S) - LAMBDA*(D/D0)^2 - MU*softplus(V/V0-1,k)
            - ETA * min(1, conc_ramp_days/CONC_RAMP_DAYS) * max(0, K-K0).

    Rewards added, risk penalties subtracted. The constant log-hurdle term is omitted
    (it shifted every U equally and is enforced only in the eligibility gate). The
    concentration penalty's WEIGHT ramps with ``conc_ramp_days`` = 1 + non-profit days
    (see CONC_RAMP_DAYS): it advances ONLY on days that added no profit, so a run of
    profitable days freezes it at the day-1 level (1/14) and a one-day-wonder that then
    goes idle still reaches full weight."""
    ramp = 1.0 if conc_ramp_days is None else min(1.0, conc_ramp_days / CONC_RAMP_DAYS)
    return_term = math.log1p(R) if (1.0 + R) > 0.0 else -50.0
    absolute_pnl_term = BETA * math.log1p(max(0.0, X) / S)
    drawdown_term = -LAMBDA * (D / D0) ** 2
    downside_volatility_term = -MU * softplus(V / V0 - 1.0, K_SOFTPLUS)
    concentration_term = -ETA * ramp * max(0.0, K - K0)

    U = (
        return_term
        + absolute_pnl_term
        + drawdown_term
        + downside_volatility_term
        + concentration_term
    )
    U_plus = max(0.0, U)
    return UtilityTerms(
        return_term=return_term,
        absolute_pnl_term=absolute_pnl_term,
        drawdown_term=drawdown_term,
        downside_volatility_term=downside_volatility_term,
        concentration_term=concentration_term,
        utility_U=U,
        U_plus=U_plus,
        performance_score_G=performance_transform(U_plus),
    )


# ---------------------------------------------------------------------------
# Punishment multiplier M = min(M_P, M_EV)  (penalties do NOT stack)
#
# Behavioral-discipline haircuts (recent PnL, recent activity), each a 50% cut: if
# EITHER fires the score is halved ONCE (the worst single haircut applies), never
# compounded to 0.25. Risk shaping lives entirely in U -- there is no M_D multiplier
# (it would double-count the -LAMBDA*(D/D0)^2 term on the same metric D).
# ---------------------------------------------------------------------------

@dataclass
class Punishment:
    M_P: float
    M_EV: float
    M_total: float


def pnl30_multiplier(rolling_30d_pnl: float) -> float:
    """M_P = 0.50 if rolling 30d net PnL < 0 else 1.00."""
    return P30_PENALTY if rolling_30d_pnl < 0.0 else 1.00


def executed_value_multiplier(Q_EV: float) -> float:
    """M_EV = 0.50 if Q_EV < 4 else 1.00."""
    return EV_PENALTY if Q_EV < Q_EV_THRESHOLD else 1.00


def compute_punishment(rolling_30d_pnl: float, Q_EV: float) -> Punishment:
    M_P = pnl30_multiplier(rolling_30d_pnl)
    M_EV = executed_value_multiplier(Q_EV)
    return Punishment(M_P=M_P, M_EV=M_EV, M_total=min(M_P, M_EV))  # do NOT stack -- worst applies


# ---------------------------------------------------------------------------
# Final score assembly
# ---------------------------------------------------------------------------

@dataclass
class FinalScore:
    eligible: bool
    eligibility_failures: list[str]
    eliminated: bool
    elimination_reasons: list[str]
    metrics: Metrics
    utility: UtilityTerms
    punishment: Punishment
    raw_score: float
    countable_score: float


def evaluate(records: list[DailyRecord], account_type: str,
             coins: list[str] | None = None,
             gap_dates: set[dt.date] | None = None,
             end_date: dt.date | None = None) -> FinalScore:
    """Full per-account evaluation on the daily series.

        raw_score       = G * M_total                       (always computed)
        countable_score = raw_score if (not eliminated AND eligible) else 0

    ``account_type`` ("human"/"agent") only changes the eligibility floors. ``coins``
    feeds the trading-restriction rule (omit -> skipped). ``gap_dates`` feeds the
    dead-agent rule. ``end_date`` defaults to the last record's date; records after
    it are dropped so a misaligned series can't leak future data into the score.

    The data layer should supply a CONTINUOUS daily series (one record per UTC day,
    equity carried forward, volume 0 on no-trade days); the first record is the
    baseline, not a PnL day.
    """
    if not records:
        return FinalScore(
            eligible=False, eligibility_failures=["no data"],
            eliminated=False, elimination_reasons=[],
            metrics=compute_metrics(records, end_date or dt.date.min),
            utility=compute_utility(0, 0, 0, 0, 1.0),
            punishment=compute_punishment(0, 0),
            raw_score=0.0, countable_score=0.0,
        )

    # Normalize to one record per UTC day, ascending. Duplicate dates are collapsed
    # DETERMINISTICALLY (sort by (date, equity), last-wins -> max-equity winner) so
    # the score never depends on input ordering.
    by_date: dict[dt.date, DailyRecord] = {}
    for r in sorted(records, key=lambda r: (r.date, r.equity)):
        by_date[r.date] = r
    records = list(by_date.values())
    end_date = end_date or records[-1].date
    # Drop post-snapshot records: no future leakage into A, drawdown, rolling windows.
    records = [r for r in records if r.date <= end_date]
    if not records:
        return FinalScore(
            eligible=False, eligibility_failures=["no data on/before end_date"],
            eliminated=False, elimination_reasons=[],
            metrics=compute_metrics([], end_date),
            utility=compute_utility(0, 0, 0, 0, 1.0),
            punishment=compute_punishment(0, 0),
            raw_score=0.0, countable_score=0.0,
        )

    elim = check_elimination(records, end_date, coins, gap_dates)
    m = compute_metrics(records, end_date, gap_dates)
    eligibility_failures = check_eligibility(account_type, m)
    eligible = not eligibility_failures
    util = compute_utility(m.R, m.X, m.D, m.V, m.K, m.conc_ramp_days)
    pun = compute_punishment(m.rolling_30d_pnl, m.Q_EV)

    raw_score = util.performance_score_G * pun.M_total
    if not math.isfinite(raw_score):   # defense-in-depth: never emit nan/inf
        raw_score = 0.0
    countable = raw_score if (not elim.eliminated and eligible) else 0.0

    return FinalScore(
        eligible=eligible,
        eligibility_failures=eligibility_failures,
        eliminated=elim.eliminated,
        elimination_reasons=elim.reasons,
        metrics=m,
        utility=util,
        punishment=pun,
        raw_score=raw_score,
        countable_score=countable,
    )
