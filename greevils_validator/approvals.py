"""Agent image-digest approval, governed by the highest-staked validator's on-chain commitment.

Being a *valid* agent (live + healthy + attested, see api_client) is necessary but not
sufficient for agent rewards: the agent's image digest must also be **approved**.

The approved set can grow without bound (one digest per agent), but an on-chain commitment is
capped at 128 bytes -- so the set is NOT stored on-chain. Instead:

  * Any hotkey owner publishes its approved list to greevils-api (`POST /approved/{hotkey}`,
    authenticated by an sr25519 signature) and commits `gva1:<base64(sha256(canonical_list))>`
    on-chain (`greevils approve`).
  * The validator honours only the **highest-staked validator-permit holder**. It fetches that
    hotkey's list from greevils-api (`GET /approved/{hotkey}`), recomputes the hash, and checks
    it matches the on-chain commitment. If it matches, every digest in the list is approved; if
    anything fails to verify, NOTHING is approved (so the agent arena burns rather than paying
    an unverified set).

The canonical serialization + hash here MUST stay byte-for-byte identical to greevils-cli
(greevils_cli/approve.py) and greevils-api (app/approvals.py), or the on-chain hash won't
verify against the fetched list.
"""
import base64
import hashlib
import json
import logging

import requests

logger = logging.getLogger(__name__)

# Namespace tag marking a commitment as a greevils approval hash (v1).
APPROVAL_TAG = "gva1:"

# Guard rails for the greevils-api fetch.
FETCH_TIMEOUT = 30                 # seconds
MAX_LIST_BYTES = 8 * 1024 * 1024   # refuse absurdly large lists (DoS guard)

# Cache verified lists by their on-chain hash: an unchanged approval (same hash) is served from
# memory with no refetch, so a transient API hiccup can't blank approvals every round.
_LIST_CACHE: dict[str, set[str]] = {}


# --- canonical serialization (keep identical to greevils-cli + greevils-api) ----------------

def normalize_digest(digest: str) -> str:
    """Canonicalize one image digest: drop any `algo:` prefix, lowercase, trim."""
    return digest.strip().lower().rsplit(":", 1)[-1]


def canonical_digests(digests: list[str]) -> list[str]:
    """Normalize, drop blanks, dedupe and sort -- the order-independent canonical form."""
    return sorted({normalize_digest(d) for d in digests if isinstance(d, str) and d.strip()})


def list_hash_b64(digests: list[str]) -> str:
    """base64(sha256(json.dumps(canonical_digests))) -- the value committed on-chain."""
    blob = json.dumps(canonical_digests(digests), separators=(",", ":")).encode()
    return base64.b64encode(hashlib.sha256(blob).digest()).decode()


# --- chain + api ----------------------------------------------------------------------------

def highest_staked_validator(metagraph) -> str | None:
    """Hotkey ss58 of the validator-permit holder with the most stake, or None if there is none."""
    best_hotkey, best_stake = None, -1.0
    for uid in range(len(metagraph.hotkeys)):
        if not bool(metagraph.validator_permit[uid]):
            continue
        stake = float(metagraph.stake[uid])
        if stake > best_stake:
            best_hotkey, best_stake = metagraph.hotkeys[uid], stake
    return best_hotkey


def parse_commitment(commitment: str) -> str | None:
    """Extract the base64 sha256 from a `gva1:<hash>` commitment, or None if it isn't one."""
    if not commitment.startswith(APPROVAL_TAG):
        return None
    hash_b64 = commitment[len(APPROVAL_TAG):].strip()
    try:
        if len(base64.b64decode(hash_b64, validate=True)) != hashlib.sha256().digest_size:
            return None
    except (ValueError, TypeError):
        return None
    return hash_b64


def fetch_approved_list(api_url: str, hotkey: str) -> list[str] | None:
    """GET the approved-digest list a hotkey published to greevils-api, or None on failure."""
    try:
        resp = requests.get(f"{api_url}/approved/{hotkey}", timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("approval list fetch failed for %s: %s", hotkey, e)
        return None
    if len(resp.content) > MAX_LIST_BYTES:
        logger.warning("approval list for %s too large (%d bytes) -- ignoring", hotkey, len(resp.content))
        return None
    try:
        data = resp.json()
        digests = data["digests"] if isinstance(data, dict) else data
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("approval list for %s is malformed: %s", hotkey, e)
        return None
    return digests if isinstance(digests, list) else None


def get_approved_digests(subtensor, metagraph, netuid: int, api_url: str) -> set[str]:
    """Return the set of approved (normalized) image digests, or an empty set if none.

    Empty means "nothing approved this round" -- no validator-permit holder, no/invalid
    approval commitment, a fetch failure, or a hash mismatch between the on-chain commitment and
    the greevils-api list.
    """
    hotkey = highest_staked_validator(metagraph)
    if hotkey is None:
        logger.warning("no validator-permit holder found -- no agent digests approved")
        return set()

    committed = parse_commitment(subtensor.get_all_commitments(netuid).get(hotkey, ""))
    if committed is None:
        logger.info("top validator %s has no approval commitment -- nothing approved", hotkey)
        return set()
    if committed in _LIST_CACHE:
        return _LIST_CACHE[committed]

    raw_list = fetch_approved_list(api_url, hotkey)
    if raw_list is None:
        return set()  # don't cache failures; retry the same hash next round
    if list_hash_b64(raw_list) != committed:
        logger.warning("approval list hash for %s does not match its on-chain commitment "
                       "-- ignoring", hotkey)
        return set()

    approved = set(canonical_digests(raw_list))
    _LIST_CACHE[committed] = approved
    logger.info("top validator %s approves %d image digest(s)", hotkey, len(approved))
    return approved
