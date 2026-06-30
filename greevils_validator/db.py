"""The validator's own daily database -- the source of truth for scoring.

Each UTC day the validator records one row per account by snapshotting the Hyperliquid API
at the day boundary (~00:00 UTC, where the snapshot equals the day's close). A live snapshot
is exact (HL's own numbers):

    equity   = clearinghouseState.accountValue                 (exact)
    volume   = sum |px*sz| of that day's fills                  (exact; "active" = volume>0)
    net_flow = (equity_today - equity_yesterday) - trading_pnl  (exact, by residual)
      where trading_pnl = realized(fills) - fees(fills) + funding + (unreal_today - unreal_yest)

Deriving net_flow as the residual is the trick: anything that moved equity but wasn't
trading IS capital flow, so no spot/perp ledger classification is needed and the result is
exact. (unreal is stored per row so tomorrow's residual can use it.)

The validator scores from this stored database, never a fresh re-fetch. Reconstruction
(hl_data.build_miner_data -- the not-100%-accurate engine) is used ONLY to backfill days the
validator could not snapshot live (cold-start history, or a gap from downtime); those rows
are source='backfill', while live rows are 'live' and authoritative.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3

from .hl_data import _clearinghouse, _post, build_miner_data
from .scoring import DailyRecord
from .tournament import MinerData

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    address TEXT NOT NULL, day TEXT NOT NULL,
    equity REAL NOT NULL, net_flow REAL NOT NULL, volume REAL NOT NULL,
    unreal REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (address, day)
);
CREATE TABLE IF NOT EXISTS observations (
    address TEXT NOT NULL, coin TEXT NOT NULL,
    PRIMARY KEY (address, coin)
);
CREATE TABLE IF NOT EXISTS disqualified (
    address TEXT PRIMARY KEY, reason TEXT NOT NULL, since TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _day_ms(day: dt.date) -> tuple[int, int]:
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    return int(start.timestamp() * 1000), int((start + dt.timedelta(days=1)).timestamp() * 1000)


# A live snapshot's net_flow residual is only trustworthy if taken near the day boundary (so the
# equity/unreal ARE the day's close). Beyond this drift we keep a flow-neutral baseline instead.
_LIVE_BOUNDARY_TOLERANCE = 1800  # seconds


def _prev_row(conn, address: str, day: dt.date):
    cur = conn.execute(
        "SELECT day, equity, unreal, source FROM daily WHERE address=? AND day<? "
        "ORDER BY day DESC LIMIT 1",
        (address, day.isoformat()))
    return cur.fetchone()


def _bump_observations(conn, address: str, coins: set[str]) -> None:
    """Persist every coin traded so the trading-restriction DQ (spot / off-whitelist pair) sees it."""
    for coin in coins:
        conn.execute(
            "INSERT INTO observations(address,coin) VALUES(?,?) ON CONFLICT(address,coin) DO NOTHING",
            (address, coin))


def is_disqualified(conn, address: str) -> bool:
    """True if this account has been permanently eliminated (e.g. traded off our builder app)."""
    return conn.execute("SELECT 1 FROM disqualified WHERE address=?",
                        (address.lower(),)).fetchone() is not None


def mark_disqualified(conn, address: str, reason: str) -> None:
    """Permanently eliminate an account. INDELIBLE: a recorded DQ is never lifted, so it
    survives feed retention dropping the offending day or a validator restart."""
    since = dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn.execute("INSERT INTO disqualified(address,reason,since) VALUES(?,?,?) "
                 "ON CONFLICT(address) DO NOTHING", (address.lower(), reason, since))
    conn.commit()


def record_live_day(conn, address: str, day: dt.date) -> None:
    """Snapshot HL now and write the EXACT row for `day` (intended ~00:00 UTC of day+1)."""
    ch = _clearinghouse(address)
    ms = ch.get("marginSummary")
    if not ms:
        return
    equity = float(ms["accountValue"])
    unreal = sum(float(p["position"].get("unrealizedPnl", 0) or 0) for p in ch.get("assetPositions", []))

    s_ms, e_ms = _day_ms(day)
    day_fills = [f for f in _post({"type": "userFillsByTime", "user": address, "startTime": s_ms})
                 if s_ms <= f["time"] < e_ms]
    fund = [u for u in _post({"type": "userFunding", "user": address, "startTime": s_ms})
            if s_ms <= u["time"] < e_ms]

    volume = sum(abs(float(f["px"]) * float(f["sz"])) for f in day_fills)
    realized = sum(float(f.get("closedPnl", 0)) for f in day_fills)
    fees = sum(float(f.get("fee", 0)) for f in day_fills)
    funding = sum(float(u.get("delta", {}).get("usdc", 0)) for u in fund)

    prev = _prev_row(conn, address, day)
    boundary = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    off_boundary = abs((dt.datetime.now(dt.timezone.utc) - boundary).total_seconds()) > _LIVE_BOUNDARY_TOLERANCE
    if prev is None or prev[3] == "backfill" or off_boundary:
        # No trustworthy residual: no prior row, OR the prior row's unreal is from candle marks
        # (a different source than this clearinghouse unreal, so the delta would be corrupt), OR
        # this snapshot was taken far from the day boundary. Fall back to a flow-neutral baseline.
        net_flow = 0.0
    else:
        _, prev_eq, prev_unreal, _ = prev
        trading_pnl = realized - fees + funding + (unreal - prev_unreal)
        net_flow = (equity - prev_eq) - trading_pnl

    conn.execute(
        "INSERT INTO daily(address,day,equity,net_flow,volume,unreal,source) "
        "VALUES(?,?,?,?,?,?,'live') ON CONFLICT(address,day) DO UPDATE SET "
        "equity=excluded.equity, net_flow=excluded.net_flow, volume=excluded.volume, "
        "unreal=excluded.unreal, source='live'",
        (address, day.isoformat(), equity, net_flow, volume, unreal))
    _bump_observations(conn, address, {f["coin"] for f in day_fills})


def backfill(conn, address: str, account_type: str, upto: dt.date) -> None:
    """Reconstruct days the validator couldn't snapshot live (cold-start / gaps) and store
    them as source='backfill' (NOT overwriting any 'live' row). Uses the not-100% engine."""
    md, unreal_by_day = build_miner_data(address, account_type)
    have = {r[0] for r in conn.execute("SELECT day FROM daily WHERE address=?", (address,))}
    for rec in md.records:
        if rec.date > upto or rec.date.isoformat() in have:
            continue
        conn.execute(
            "INSERT INTO daily(address,day,equity,net_flow,volume,unreal,source) "
            "VALUES(?,?,?,?,?,?,'backfill') ON CONFLICT(address,day) DO NOTHING",
            (address, rec.date.isoformat(), rec.equity, rec.net_flow, rec.volume,
             unreal_by_day.get(rec.date, 0.0)))
    _bump_observations(conn, address, set(md.observations or []))


def update_account(conn, address: str, account_type: str) -> None:
    """Bring the DB current for one account: record the latest complete day LIVE, and
    backfill (reconstruct) any earlier missing days. Idempotent; cheap in steady state."""
    sealed = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)  # last complete UTC day
    have = {r[0] for r in conn.execute("SELECT day FROM daily WHERE address=?", (address,))}
    # Backfill only when there's a real gap below `sealed` (cold start or downtime),
    # so steady state never re-reconstructs history.
    if sealed.isoformat() not in have:
        prior_missing = not have or max(have) < (sealed - dt.timedelta(days=1)).isoformat()
        if prior_missing:
            backfill(conn, address, account_type, upto=sealed)
        record_live_day(conn, address, sealed)
    conn.commit()


def load_miner_data(conn, address: str, account_type: str) -> MinerData:
    """Read the stored daily rows + observations into a MinerData for scoring."""
    rows = conn.execute(
        "SELECT day,equity,net_flow,volume FROM daily WHERE address=? ORDER BY day",
        (address,)).fetchall()
    records = [DailyRecord(dt.date.fromisoformat(d), eq, nf, vol)
               for d, eq, nf, vol in rows]
    # Days missing BETWEEN stored rows are genuine data outages (downtime that backfill couldn't
    # reconstruct), not no-trade days -- mark them so the dead-agent check doesn't count them as
    # inactivity and wrongly eliminate the account.
    gap_dates: set = set()
    for prev, cur in zip(records, records[1:]):
        d = prev.date + dt.timedelta(days=1)
        while d < cur.date:
            gap_dates.add(d)
            d += dt.timedelta(days=1)
    obs = [c for (c,) in conn.execute(
        "SELECT coin FROM observations WHERE address=?", (address,))]
    return MinerData(address, records, account_type, observations=obs, gap_dates=gap_dates)
