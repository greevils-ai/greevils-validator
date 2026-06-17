"""Reward computation -- THE ONE FUNCTION YOU IMPLEMENT.

`calculate_rewards` is called once per arena per round, with the list of Hyperliquid account
addresses competing in that arena (all agents in one call, all humans in another). The two
arenas use the exact same logic -- they only differ in who is in the list.

Contract:
  * input:  addresses -- a list of Hyperliquid account addresses (lowercased), order matters
  * output: a list of floats, SAME LENGTH and SAME ORDER as `addresses`, that EITHER
              - sums to 1.0 (relative shares of this arena's emission), OR
              - is all zeros (nobody in this arena earned anything -> the arena's whole
                emission share is burned to UID 0 by the caller).
  * a single non-negative reward per address; the caller normalizes defensively.

Everything around this function -- reading commitments, agent/human classification, the
90/10 split, burning, weight-setting -- is already wired up. Implement the comparison here.
"""
import logging

logger = logging.getLogger(__name__)


def calculate_rewards(addresses: list[str]) -> list[float]:
    """PLACEHOLDER -- replace with the real arena scoring.

    Returns all zeros, which makes the caller burn this arena's emission share to UID 0.
    That's the safe default until the comparison is implemented: no miner is paid for work
    that hasn't been scored yet.
    """
    # TODO(greevils): score `addresses` against each other (PnL, Sharpe, ... over the round)
    # and return their relative shares. The result must sum to 1.0 (or be all zeros).
    #
    # Example of the eligible-everyone case (equal split) -- delete once real scoring lands:
    #     n = len(addresses)
    #     return [1.0 / n] * n if n else []
    logger.warning("calculate_rewards is a placeholder -- returning all zeros (arena will burn)")
    return [0.0] * len(addresses)
