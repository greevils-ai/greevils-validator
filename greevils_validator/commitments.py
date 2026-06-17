"""Read + verify miner ownership commitments from the chain.

A miner publishes (via greevils-cli `commit`) a compact commitment to the subnet -- the
on-chain Raw field is capped at 128 bytes, so only the irreducible bytes are stored:

    base64( hl_address(20 bytes) || signature(65 bytes) )   -- 116 chars

`signature` is an EIP-191 personal_sign over the *canonical message* by the Hyperliquid
account's key. The message itself is NOT stored: we rebuild it here from the committing
hotkey + the embedded `hl_address` (see `canonical_message`) and accept the claim iff the
signature recovers to `hl_address`. Rebuilding from the *committing* hotkey is what binds the
claim to this miner -- a copied blob would be rebuilt with the copier's hotkey, so its
signature would no longer recover to `hl_address` and the copy is rejected.

Keep `canonical_message` + this decoding byte-for-byte in agreement with
greevils-cli/greevils_cli/commit.py and the TEE harness (builder/harness.py).
"""
import base64
import logging

logger = logging.getLogger(__name__)

# Fixed byte widths packed into the on-chain blob (address || signature).
_ADDR_BYTES = 20
_SIG_BYTES = 65


def canonical_message(hotkey_ss58: str, hl_address: str) -> str:
    """Rebuild the exact message the miner signed. MUST match greevils-cli's commit.py."""
    return (
        "Greevils Hyperliquid ownership claim\n"
        f"hotkey: {hotkey_ss58}\n"
        f"hyperliquid: {hl_address.lower()}"
    )


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


def _decode_commitment(raw: str) -> tuple[str, str] | None:
    """Unpack the base64 blob into (hl_address, signature) 0x-hex, or None if malformed."""
    try:
        blob = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None
    if len(blob) != _ADDR_BYTES + _SIG_BYTES:
        return None
    return "0x" + blob[:_ADDR_BYTES].hex(), "0x" + blob[_ADDR_BYTES:].hex()


def parse_and_verify(hotkey_ss58: str, raw: str) -> str | None:
    """Validate one miner's raw on-chain commitment string.

    Returns the claimed Hyperliquid address (lowercased) if the commitment is well-formed and
    correctly signed for this hotkey; otherwise None.
    """
    decoded = _decode_commitment(raw)
    if decoded is None:
        logger.debug("hotkey %s: unparseable/wrong-size commitment", hotkey_ss58)
        return None
    hl_address, signature = decoded

    # Rebuild the signed message from THIS hotkey; a copied blob rebuilds to a different
    # message and fails recovery, so the claim is bound to the committing miner for free.
    message = canonical_message(hotkey_ss58, hl_address)
    if not verify_signature(message, signature, hl_address):
        logger.warning("hotkey %s: bad ownership signature for %s -- ignoring", hotkey_ss58, hl_address)
        return None

    return hl_address.lower()


def collect_verified_claims(subtensor, metagraph, netuid: int) -> list[tuple[int, str, str]]:
    """Read every registered miner's commitment and keep the ones that verify.

    Returns a list of (uid, hotkey_ss58, hl_address) for miners with a valid claim.
    """
    commitments = subtensor.get_all_commitments(netuid)  # {hotkey_ss58: raw_str}
    logger.info("fetched %d commitments", len(commitments))
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
