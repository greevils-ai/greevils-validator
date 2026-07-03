"""Validator configuration -- emission split, cadence, and the API endpoint.

Wallet / network / netuid are passed as CLI options in validator.py (they vary per run);
everything here is policy and rarely changes. All are overridable via env.
"""
import datetime as dt
import os

# Same default as greevils-cli.
GREEVILS_API = os.getenv("GREEVILS_API", "https://api.greevils.ai")

# Fixed Bittensor constants (block time is protocol-wide; a scoring round is one UTC day; tempo is
# read from chain at runtime, EPOCH_TEMPO_FALLBACK is used only if that read fails).
#   - SCORING runs once per UTC day at the 00:00 boundary; a round's emission = per-block rate x
#     BLOCKS_PER_ROUND (subnet emission fields are per-block).
#   - WEIGHTS are re-set every epoch (tempo) so last_update stays within activity_cutoff and the
#     validator's weights keep counting in consensus.
BLOCK_TIME_SECONDS = 12                                 # seconds per block
BLOCKS_PER_ROUND = 24 * 60 * 60 // BLOCK_TIME_SECONDS    # 7200: blocks of emission in one daily round
EPOCH_TEMPO_FALLBACK = 360                              # epoch length (blocks) if tempo can't be read

# Human-arena emission cap. One unified tournament over agents AND humans, split into a human
# pool and an agent pool distributed by the SAME unified score. The human pool is sized in
# DOLLAR terms; agents absorb the rest:
#
#     human_share = min(HUMAN_SHARE_CAP, HUMAN_PNL_K * human_PnL$ / emission_USD)
#     agent_share = 1 - human_share
#
# PnL and emission are measured over the SAME horizon (PNL_WINDOW_DAYS). Whatever humans don't
# take goes to AGENTS (or burns if there are no eligible agents).
HUMAN_SHARE_CAP = max(0.0, min(1.0, float(os.getenv("HUMAN_SHARE_CAP", "0.50"))))  # ceiling, clamped to [0,1]
HUMAN_PNL_K = float(os.getenv("HUMAN_PNL_K", "0.1"))            # emission-USD <= k * human PnL-USD
PNL_WINDOW_DAYS = int(os.getenv("PNL_WINDOW_DAYS", "1"))        # PnL/emission horizon (1 = that day)

# TAO->USD price for the human cap's emission_USD. Precision is not critical (the cap is coarse).
# Any failure -> human cap 0 for the round (agents absorb), never a crash. See pricing.py.
TAO_USD_URL = os.getenv(
    "TAO_USD_URL",
    "https://api.coingecko.com/api/v3/simple/price?ids=bittensor&vs_currencies=usd",
)
TAO_USD_FALLBACK = float(os.getenv("TAO_USD_FALLBACK", "0"))    # 0 -> treat as unavailable
# Optional manual overrides for the two on-chain pieces (set if the SDK reads are unavailable).
EMISSION_ALPHA_PER_ROUND = float(os.getenv("EMISSION_ALPHA_PER_ROUND", "0"))  # 0 -> read on-chain
ALPHA_TAO_PRICE = float(os.getenv("ALPHA_TAO_PRICE", "0"))                    # 0 -> read on-chain

# Daily database (SQLite): one exact row per account per UTC day, snapshotted at the day
# boundary; reconstruction only backfills days it couldn't snapshot live. See db.py.
DB_PATH = os.getenv("GREEVILS_DB", "greevils_validator.sqlite")

# Where un-awarded emissions go. UID 0 is the subnet owner by convention -- weighting it
# "burns" the corresponding emission.
BURN_UID = int(os.getenv("BURN_UID", "0"))

# Builder exclusivity: a miner must route EVERY trade through OUR builder app, or be eliminated
# (a hard pre-gate, before any scoring). Checked REAL-TIME from each fill's `builderFee`: a fill
# routed through us carries a builder fee at BUILDER_FEE_BPS; no fee (traded directly on HL) or a
# different rate (another builder) is off-app. See builder.py.
BUILDER_FEE_BPS = float(os.getenv("BUILDER_FEE_BPS", "5"))          # our builder's fee (basis points)
BUILDER_FEE_TOL_BPS = float(os.getenv("BUILDER_FEE_TOL_BPS", "0.5"))  # match tolerance (fee rounding)
BUILDER_MIN_NOTIONAL = float(os.getenv("BUILDER_MIN_NOTIONAL", "1"))  # $; skip dust (fee may round to 0)
# Rule's effective date: pre-join HL history isn't counted against a miner. Empty -> entire history.
_bx = os.getenv("BUILDER_EXCLUSIVITY_START", "").strip()
try:
    BUILDER_EXCLUSIVITY_START = dt.date.fromisoformat(_bx) if _bx else None
except ValueError:
    BUILDER_EXCLUSIVITY_START = None  # ignore a malformed date rather than crash at import

# An agent submission counts as a *valid greevil agent* only if greevils-api reports all of
# these for it. Anything else (or an address greevils-api has never seen) is a human account.
AGENT_REQUIRED_STATUS = "RUNNING"
AGENT_REQUIRED_HEALTH = "HEALTHY"
AGENT_REQUIRED_ATTESTATION = "PASS"

# Heartbeat watchdog: if the main loop hasn't checked in for this long, re-exec the process.
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", "600"))
