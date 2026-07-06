"""Hyperliquid native-API data layer: build a per-account daily series for scoring.

The adapter behind rewards._fetch_miner_data: turns HL's public info API into the
`DailyRecord(date, equity, net_flow, volume)` series the factor engine consumes, plus
`observations` (coins traded) for the trading rule.

Basis = PERP-ONLY (spot balances are ignored). Equity and PnL come straight from HL's own
`portfolio` endpoint (the PERP `accountValueHistory` / `pnlHistory`) -- they are NOT reconstructed
from ledger flows, so spot<->perp transfers are handled automatically: the residual
`net_flow = ΔaccountValue - Δpnl` captures any capital movement with no classification, and the
day's return is exactly HL's own `Δpnl`. Volume comes from fills.

HL's history points are irregular (~1-9h apart), so a day-close value can be a few hours stale --
i.e. this is APPROXIMATE and is used only to BACKFILL days the validator could not snapshot live
(cold start / downtime). The latest day is anchored to clearinghouseState (exact). HIP-3 / stock /
forex coin strings flow straight through to scoring (the trading rule normalizes + whitelists them).
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import logging
import time
import urllib.request
from collections import defaultdict

from .scoring import DailyRecord
from .tournament import MinerData

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
_TIMEOUT = 30
_PAGE = 2000          # userFillsByTime page size
_DAY_MS = 86_400_000  # one UTC day in ms


def _post(body: dict, retries: int = 3) -> object:
    """POST to the HL info endpoint with simple retry. Returns parsed JSON."""
    err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                INFO_URL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # transient network / rate-limit
            err = e
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"HL info {body.get('type')} failed: {err}")


def _day(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


# ---------------------------------------------------------------------------
# Raw fetchers
# ---------------------------------------------------------------------------

def _fills(address: str) -> list[dict]:
    """All fills, ascending by time (paginated)."""
    out: list[dict] = []
    start = 0
    seen: set = set()
    while True:
        chunk = _post({"type": "userFillsByTime", "user": address, "startTime": start})
        if not chunk:
            break
        for f in chunk:
            key = (f.get("hash"), f.get("oid"), f.get("tid"))
            if key not in seen:
                seen.add(key)
                out.append(f)
        if len(chunk) < _PAGE:
            break
        # Re-anchor AT the last fill's timestamp (not +1) so fills sharing that millisecond across
        # the page boundary aren't skipped; `seen` dedupes the overlap. Guard the degenerate case
        # of a whole full page at one timestamp (can't advance by time) to avoid an infinite loop.
        last_t = chunk[-1]["time"]
        start = last_t + 1 if last_t == chunk[0]["time"] else last_t
    return out


def _clearinghouse(address: str) -> dict:
    return _post({"type": "clearinghouseState", "user": address}) or {}


def _portfolio(address: str) -> list:
    """HL's `portfolio`: [[period, {accountValueHistory, pnlHistory, vlm}], ...]."""
    return _post({"type": "portfolio", "user": address}) or []


def fetch_allowed_perps() -> set[str]:
    """Normalized keys for EVERY tradable HL perp -- the native `meta` universe PLUS every HIP-3
    builder-dex market (from `perpDexs` -> per-dex `meta`) -- unioned into scoring.ALLOWED_PAIRS at
    startup so the whitelist mirrors all of HL automatically (our app imitates HL). HIP-3 coins are
    `dex:SYM`, normalized to the bare key. Best-effort: each fetch is independent, so one failing
    dex (or perpDexs itself) just omits those markets rather than blanking the whole set."""
    from .scoring import normalize_pair

    def _universe(body: dict) -> set[str]:
        try:
            uni = (_post(body) or {}).get("universe", [])
            return {normalize_pair(a["name"]) for a in uni if a.get("name")}
        except Exception as e:  # noqa: BLE001
            logger.warning("HL meta %s failed: %s -- those markets omitted", body, e)
            return set()

    pairs = _universe({"type": "meta"})                       # native perps
    try:
        dexs = _post({"type": "perpDexs"}) or []              # HIP-3 builder dexes
    except Exception as e:  # noqa: BLE001
        logger.warning("HL perpDexs failed: %s -- HIP-3 markets not synced", e)
        dexs = []
    for d in dexs:
        if d and d.get("name"):
            pairs |= _universe({"type": "meta", "dex": d["name"]})
    return pairs


# ---------------------------------------------------------------------------
# Assembly -- equity/PnL from HL's portfolio, volume from fills
# ---------------------------------------------------------------------------

def _perp_series(portfolio: list) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """HL's PERP account-value and cumulative-PnL histories as two time-sorted [(ms, value)]
    lists, BOTH from a SINGLE period (`perpAllTime`, falling back to the next-longest). One period
    is essential: each period's `pnlHistory` is cumulative from THAT period's start, so AV and PnL
    must share one baseline for `net_flow = ΔAV - Δpnl` to mean anything (merging mixes baselines).
    They're kept as independent carry-forward series (AV/PnL sample times can differ slightly).
    `perpAllTime` covers the full account history."""
    by_period = {}
    for entry in portfolio:
        try:
            period, data = entry
        except (ValueError, TypeError):
            continue
        if isinstance(period, str) and period.startswith("perp"):
            by_period[period] = data
    data = next((by_period[p] for p in ("perpAllTime", "perpMonth", "perpWeek", "perpDay")
                 if p in by_period), None)
    if data is None:
        return [], []
    av = sorted((int(ms), float(v)) for ms, v in data.get("accountValueHistory", []))
    pnl = sorted((int(ms), float(v)) for ms, v in data.get("pnlHistory", []))
    return av, pnl


def build_miner_data(address: str, account_type: str) -> tuple[MinerData, dict[dt.date, float]]:
    """Per-account perp-only daily series for BACKFILL, sourced from HL's own numbers.

    Returns (MinerData, {}). The empty second element is vestigial (callers still unpack it; the
    DB's backfill->live net_flow boundary is guarded in db.record_live_day, so no per-day
    unrealized is needed here). Approximate (HL history granularity); used only for days the
    validator could not snapshot live."""
    fills = _fills(address)
    av_series, pnl_series = _perp_series(_portfolio(address))
    if not av_series:
        return MinerData(address, [], account_type), {}

    # Per-day volume + coins traded (exact, from fills).
    vol: dict[dt.date, float] = defaultdict(float)
    coins_seen: set[str] = set()
    for f in fills:
        vol[_day(f["time"])] += abs(float(f["px"]) * float(f["sz"]))
        coins_seen.add(f["coin"])

    av_ts = [s[0] for s in av_series]; av_v = [s[1] for s in av_series]
    pnl_ts = [s[0] for s in pnl_series]; pnl_v = [s[1] for s in pnl_series]

    def _at(ts_list: list[int], vals: list[float], t: int) -> float | None:
        """Last value at or before t (carry-forward), or None if t precedes the series."""
        i = bisect.bisect_right(ts_list, t)
        return vals[i - 1] if i > 0 else None

    dep_i = next((i for i, s in enumerate(av_series) if s[1] > 0.0), 0)
    deposit_eq = av_series[dep_i][1]
    deposit_pnl = _at(pnl_ts, pnl_v, av_series[dep_i][0]) or 0.0
    first_day = _day(av_series[dep_i][0])
    today = dt.datetime.now(dt.timezone.utc).date()
    one = dt.timedelta(days=1)

    records: list[DailyRecord] = [DailyRecord(first_day - one, deposit_eq, 0.0, 0.0)]
    prev_eq, prev_pnl = deposit_eq, deposit_pnl
    d = first_day
    while d <= today:
        boundary = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000) + _DAY_MS
        eq = _at(av_ts, av_v, boundary)              # HL's perp equity at end-of-day d
        if eq is None:                               # day precedes the account's first portfolio point
            d += one
            continue
        pn = _at(pnl_ts, pnl_v, boundary) or 0.0     # HL's cumulative perp PnL at end-of-day d
        net_flow = (eq - prev_eq) - (pn - prev_pnl)  # residual = capital flow, spot/perp-agnostic
        records.append(DailyRecord(d, eq, net_flow, vol[d]))
        prev_eq, prev_pnl = eq, pn
        d += one

    # Anchor the latest day to exact live equity (clearinghouse); keep its residual net_flow.
    ch = _clearinghouse(address)
    if len(records) > 1 and ch.get("marginSummary"):
        true_eq = float(ch["marginSummary"]["accountValue"])
        records[-1] = DailyRecord(records[-1].date, true_eq, records[-1].net_flow, records[-1].volume)

    md = MinerData(address, records, account_type,
                   observations=sorted(coins_seen), gap_dates=set())
    return md, {}
