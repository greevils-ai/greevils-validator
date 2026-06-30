"""Best-effort USD value of one round's emission -- the only input the human-arena
cap needs beyond the per-account data.

The human cap (tournament.py) bounds the human emission pool in DOLLAR terms:

    human_share <= HUMAN_PNL_K * human_PnL_USD / emission_USD

so the validator needs the USD value of the alpha it emits per round:

    emission_USD = alpha_per_round * alpha_price_in_TAO * TAO_price_in_USD
                   (on-chain:subnet)  (on-chain:AMM)      (off-chain feed)

The first two are on-chain; TAO->USD is the only external piece. Precision is NOT
critical -- the cap is coarse -- so a single public price endpoint sampled once per
round is plenty. EVERY failure path returns None; the caller then treats the human
cap as 0 for that round (humans earn nothing, agents absorb the share) and logs it.
This module never raises and never blocks the round.

All three pieces can be pinned via env (ALPHA_TAO_PRICE, EMISSION_ALPHA_PER_ROUND,
TAO_USD_FALLBACK) so the cap is fully exercisable without any live chain/feed.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from .config import (
    ALPHA_TAO_PRICE,
    BLOCKS_PER_ROUND,
    EMISSION_ALPHA_PER_ROUND,
    TAO_USD_FALLBACK,
    TAO_USD_URL,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 8  # seconds -- a slow price feed must not stall weight-setting


def tao_to_usd() -> float | None:
    """TAO price in USD from the configured public endpoint (CoinGecko by default).

    Env fallback (TAO_USD_FALLBACK) if set, else the feed once, else None. Expects the
    CoinGecko {"bittensor": {"usd": <x>}} shape; any other shape returns None."""
    if TAO_USD_FALLBACK > 0:
        return TAO_USD_FALLBACK
    try:
        with urllib.request.urlopen(TAO_USD_URL, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        price = float(data["bittensor"]["usd"])
        return price if price > 0 else None
    except Exception as exc:  # network down, rate-limited, shape changed -- all non-fatal
        logger.warning("TAO->USD price fetch failed (%s) -- human cap unavailable this round", exc)
        return None


def alpha_price_tao(subtensor, netuid: int) -> float | None:
    """Price of one alpha (subnet token) in TAO, from the subnet's AMM pool.

    Env ALPHA_TAO_PRICE overrides. Otherwise read on-chain; the exact SDK accessor
    varies by bittensor version (e.g. ``subtensor.subnet(netuid).price`` on dynamic
    subnets), so the read is best-effort and returns None on any failure."""
    if ALPHA_TAO_PRICE > 0:
        return ALPHA_TAO_PRICE
    try:
        # TODO(chain): accessor varies by bittensor version (dTAO pool price is
        # tao_in/alpha_in, often subnet(netuid).price); wrapped so a mismatch -> None.
        subnet = subtensor.subnet(netuid)
        price = float(getattr(subnet, "price", None))
        return price if price > 0 else None
    except Exception as exc:
        logger.warning("alpha->TAO price read failed (%s) -- human cap unavailable this round", exc)
        return None


def alpha_emitted_per_round(subtensor, netuid: int) -> float | None:
    """Alpha emitted to the subnet over one evaluation round.

    Env EMISSION_ALPHA_PER_ROUND overrides. Otherwise best-effort on-chain; returns
    None on any failure (the cap then falls back to 0 for the round)."""
    if EMISSION_ALPHA_PER_ROUND > 0:
        return EMISSION_ALPHA_PER_ROUND
    try:
        # alpha_out_emission is a PER-BLOCK rate (Balance -> float gives tao-denominated alpha),
        # so the round's emission = per_block * BLOCKS_PER_ROUND. Pin with EMISSION_ALPHA_PER_ROUND
        # if the SDK field/units differ on your version.
        subnet = subtensor.subnet(netuid)
        per_block = getattr(subnet, "alpha_out_emission", None) or getattr(subnet, "emission", None)
        value = float(per_block) * BLOCKS_PER_ROUND
        return value if value > 0 else None
    except Exception as exc:
        logger.warning("subnet emission read failed (%s) -- human cap unavailable this round", exc)
        return None


def emission_usd(subtensor, netuid: int) -> float | None:
    """USD value of one round's emission = alpha_per_round * alpha->TAO * TAO->USD.

    None if ANY piece is unavailable; the caller treats a None cap as 0 (humans earn
    nothing this round, agents absorb the share). Never raises."""
    alpha = alpha_emitted_per_round(subtensor, netuid)
    p_tao = alpha_price_tao(subtensor, netuid)
    p_usd = tao_to_usd()
    if not alpha or not p_tao or not p_usd:
        return None
    value = alpha * p_tao * p_usd
    return value if value > 0 else None
