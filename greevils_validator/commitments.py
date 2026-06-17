"""Read + verify miner ownership commitments from the chain.

A miner publishes (via greevils-cli `commit`) a JSON commitment to the subnet:

    {"v": 1, "hl_address": "0x..", "message": "..", "signature": "0x.."}

`signature` is an EIP-191 personal_sign over `message` by the Hyperliquid account's key.
A commitment is accepted only if BOTH hold:
  * the signature recovers to `hl_address` (proves control of that account), and
  * the signed `message` references the committing miner's hotkey ss58 (binds the claim to
    the miner, so a commitment can't be copied and replayed by someone else).

Keep this verification byte-for-byte in agreement with greevils-cli/greevils_cli/commit.py.
"""
import json
import logging

logger = logging.getLogger(__name__)

COMMITMENT_VERSION = 1


def verify_signature(message: str, signature: str, expected_address: str) -> bool:
    """True iff `signature` over `message` recovers to `expected_address`."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as e:  # noqa: BLE001 -- any malformed signature is just a failed claim
        logger.debug("signature recovery failed: %s", e)
        return False
    return recovered.lower() == expected_address.lower()


def parse_and_verify(hotkey_ss58: str, raw: str) -> str | None:
    """Validate one miner's raw on-chain commitment string.

    Returns the claimed Hyperliquid address (lowercased) if the commitment is well-formed,
    bound to this hotkey, and correctly signed; otherwise None.
    """
    try:
        data = json.loads(raw)
        hl_address = data["hl_address"]
        message = data["message"]
        signature = data["signature"]
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("hotkey %s: unparseable commitment (%s)", hotkey_ss58, e)
        return None

    # Ownership-binding: the signed message must reference the committing hotkey, else a miner
    # could copy another miner's commitment verbatim and claim the same account.
    if hotkey_ss58 not in message:
        logger.warning("hotkey %s: commitment not bound to this hotkey -- ignoring", hotkey_ss58)
        return None

    if not verify_signature(message, signature, hl_address):
        logger.warning("hotkey %s: bad ownership signature for %s -- ignoring", hotkey_ss58, hl_address)
        return None

    return hl_address.lower()


def collect_verified_claims(subtensor, metagraph, netuid: int) -> list[tuple[int, str, str]]:
    """Read every registered miner's commitment and keep the ones that verify.

    Returns a list of (uid, hotkey_ss58, hl_address) for miners with a valid claim.
    """
    commitments = subtensor.get_all_commitments(netuid)  # {hotkey_ss58: raw_str}
    claims: list[tuple[int, str, str]] = []
    for uid, hotkey in enumerate(metagraph.hotkeys):
        raw = commitments.get(hotkey)
        if not raw:
            continue
        hl_address = parse_and_verify(hotkey, raw)
        if hl_address:
            claims.append((uid, hotkey, hl_address))
    logger.info("verified %d/%d miner ownership claims", len(claims), len(metagraph.hotkeys))
    return claims
