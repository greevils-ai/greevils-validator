"""One evaluation round: classify miners into arenas and turn scores into per-UID weights.

This is the glue between the verified ownership claims, the agent/human classification, and
`calculate_rewards`. It produces (uids, weights) ready to hand to subtensor.set_weights.
"""
import logging

from .api_client import fetch_valid_agents
from .approvals import get_approved_digests, normalize_digest
from .commitments import collect_verified_claims
from .config import AGENT_SHARE, BURN_UID, HUMAN_SHARE
from .rewards import calculate_rewards

logger = logging.getLogger(__name__)


def _award_arena(arena: str, claims: list[tuple[int, str]], share: float,
                 weights: dict[int, float]) -> None:
    """Distribute one arena's emission `share` across its miners, or burn it.

    `claims` is a list of (uid, hl_address) for the miners in this arena. Adds to `weights`
    in place. If the arena is empty, or `calculate_rewards` returns all zeros / a malformed
    result, the whole `share` is burned to BURN_UID.
    """
    if not claims:
        logger.info("%s arena: no miners -- burning %.2f%% to UID %d", arena, share * 100, BURN_UID)
        weights[BURN_UID] = weights.get(BURN_UID, 0.0) + share
        return

    addresses = [hl for _, hl in claims]
    rewards = calculate_rewards(addresses)

    if len(rewards) != len(addresses):
        logger.error("%s arena: calculate_rewards returned %d rewards for %d addresses -- burning",
                     arena, len(rewards), len(addresses))
        weights[BURN_UID] = weights.get(BURN_UID, 0.0) + share
        return

    total = sum(rewards)
    if total <= 0:
        logger.info("%s arena: no eligible winners -- burning %.2f%% to UID %d",
                    arena, share * 100, BURN_UID)
        weights[BURN_UID] = weights.get(BURN_UID, 0.0) + share
        return

    # Normalize defensively: the contract says rewards sum to 1, but dividing by `total`
    # keeps us correct even if an implementation returns unnormalized non-negative scores.
    for (uid, _), reward in zip(claims, rewards):
        if reward > 0:
            weights[uid] = weights.get(uid, 0.0) + share * (reward / total)
    logger.info("%s arena: awarded %.2f%% across %d miner(s)", arena, share * 100, len(claims))


def run_evaluation_round(subtensor, metagraph, netuid: int, api_url: str) -> tuple[list[int], list[float]]:
    """Run one full round and return (uids, weights) summing to ~1.0.

    Steps: verify ownership claims -> classify agent vs human via greevils-api + on-chain
    approval -> score each arena with calculate_rewards -> apply the 90/10 split, burning
    empty/loser arenas.

    An account is an *agent* only if it is BOTH a valid agent (greevils-api reports it
    RUNNING/HEALTHY/PASS) AND running an image digest approved by the highest-staked validator
    on-chain. A valid-but-unapproved agent -- like any non-agent account -- falls into humans.
    """
    claims = collect_verified_claims(subtensor, metagraph, netuid)
    valid_agents = fetch_valid_agents(api_url)  # {hl_address: image_digest}
    approved_digests = get_approved_digests(subtensor, metagraph, netuid, api_url)

    def is_approved_agent(hl: str) -> bool:
        digest = valid_agents.get(hl)
        return bool(digest) and normalize_digest(digest) in approved_digests

    agents = [(uid, hl) for uid, _, hl in claims if is_approved_agent(hl)]
    humans = [(uid, hl) for uid, _, hl in claims if not is_approved_agent(hl)]
    logger.info("classified %d approved agent(s) and %d human(s) (%d valid agent(s), "
                "%d approved digest(s))",
                len(agents), len(humans), len(valid_agents), len(approved_digests))

    weights: dict[int, float] = {}
    _award_arena("agents", agents, AGENT_SHARE, weights)
    _award_arena("humans", humans, HUMAN_SHARE, weights)

    # Belt-and-suspenders: never set an all-zero weight vector.
    if not weights:
        weights[BURN_UID] = 1.0

    uids = sorted(weights)
    return uids, [weights[uid] for uid in uids]
