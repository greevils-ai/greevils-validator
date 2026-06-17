"""Ask greevils-api which Hyperliquid addresses are valid greevil agents.

A claimed account is a *valid agent* only if greevils-api lists a submission whose
agent_address matches and whose status / health / attestation are all green (see config).
Every other claimed address -- including any greevils-api has never heard of -- is treated
as a human trading account.
"""
import logging

import requests

from .config import (
    AGENT_REQUIRED_ATTESTATION,
    AGENT_REQUIRED_HEALTH,
    AGENT_REQUIRED_STATUS,
)

logger = logging.getLogger(__name__)


def fetch_valid_agent_addresses(api_url: str, timeout: int = 30) -> set[str]:
    """Return the set of lowercased agent_address values that are live, healthy and attested.

    Raises requests.RequestException on a network/HTTP failure -- the caller decides whether
    to skip the round (rather than silently misclassifying every agent as a human).
    """
    r = requests.get(f"{api_url}/submissions", timeout=timeout)
    r.raise_for_status()

    valid: set[str] = set()
    for s in r.json():
        if (
            s.get("status") == AGENT_REQUIRED_STATUS
            and s.get("health") == AGENT_REQUIRED_HEALTH
            and s.get("attestation") == AGENT_REQUIRED_ATTESTATION
        ):
            addr = s.get("agent_address")
            if addr:
                valid.add(addr.lower())

    logger.info("greevils-api reports %d valid agent account(s)", len(valid))
    return valid
