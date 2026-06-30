"""One evaluation round: classify miners, score them in one unified tournament, set weights.

This is the glue between the verified ownership claims, the agent/human classification, and
`calculate_rewards`. It produces (uids, weights) ready to hand to subtensor.set_weights.

There is ONE tournament over agents AND humans (they play each other); emissions split into an
agent pool and a human pool by the same unified score, with the human pool sized by the dollar
cap (see tournament.py / config.py). Whatever isn't awarded -- the human shortfall when there
are no eligible agents to absorb it, an empty field, etc. -- is burned to BURN_UID.

The agent lane is alive from day one. GRACE (no agent approved yet): every *valid* agent
(greevils-api RUNNING/HEALTHY/PASS) earns -- no eligibility gate, just elimination + measurability.
OPEN-SOURCE (the top validator has approved >=1 open-sourced agent on-chain, gva1): only approved
agents earn, under the full eligibility gate. See approvals.py / tournament.py.
"""
import logging

from . import db
from .api_client import fetch_valid_agents
from .approvals import get_approved_digests, normalize_digest
from .builder import traded_outside_builder
from .commitments import collect_verified_claims
from .config import BURN_UID, DB_PATH
from .pricing import emission_usd as compute_emission_usd
from .rewards import calculate_rewards

logger = logging.getLogger(__name__)


def _builder_exclusive_claims(claims):
    """PRE-GATE: drop (and permanently record) any miner that placed a fill OUTSIDE our builder
    app. Runs before classification/scoring -- one off-app fill eliminates the account. A miner
    already disqualified is skipped without re-checking (the DQ is indelible)."""
    conn = db.connect(DB_PATH)
    try:
        kept = []
        for claim in claims:
            hl = claim[-1]
            if db.is_disqualified(conn, hl):
                continue
            try:
                chk = traded_outside_builder(hl)
            except Exception as e:  # noqa: BLE001 - a fetch error must NOT abort the round or DQ a miner
                logger.warning("builder-exclusivity check failed for %s (%s) -- keeping; re-checked next round", hl, e)
                kept.append(claim)
                continue
            if chk.violated:
                db.mark_disqualified(conn, hl, f"traded outside builder app; e.g. {chk.off_app[:2]}")
                logger.info("DQ %s (uid %s): traded outside the builder app", hl, claim[0])
                continue
            kept.append(claim)
        return kept
    finally:
        conn.close()


def run_evaluation_round(subtensor, metagraph, netuid: int, api_url: str) -> tuple[list[int], list[float]]:
    """Run one full round and return (uids, weights) summing to ~1.0.

    An account is an *agent* iff greevils-api reports it RUNNING/HEALTHY/PASS (a valid agent);
    everything else is a human. APPROVAL (gva1 -- the top validator's review of an *open-sourced*
    agent) does NOT decide agent-vs-human; it only gates the OPEN-SOURCE phase: before any agent is
    approved every valid agent earns, and once any is, only approved agents earn (see tournament.py).
    """
    claims = _builder_exclusive_claims(collect_verified_claims(subtensor, metagraph, netuid))
    valid_agents = fetch_valid_agents(api_url)  # {hl_address: image_digest}
    approved_digests = get_approved_digests(subtensor, metagraph, netuid, api_url)

    def is_valid_agent(hl: str) -> bool:
        return hl in valid_agents  # greevils-api: RUNNING + HEALTHY + PASS

    def is_approved(hl: str) -> bool:
        # gva1: the top validator reviewed this agent's open-sourced code and approved it.
        digest = valid_agents.get(hl)
        return bool(digest) and normalize_digest(digest) in approved_digests

    # One unified field: (uid, hl_address, account_type, approved). account_type is "agent" iff the
    # account is a *valid* agent; `approved` (open-sourced + reviewed) only gates the open-source
    # phase. Order is preserved through calculate_rewards so we can zip weights back onto uids.
    traders = []
    for uid, _, hl in claims:
        is_agent = is_valid_agent(hl)
        account_type = "agent" if is_agent else "human"
        approved = is_agent and is_approved(hl)
        traders.append((uid, hl, account_type, approved))
    n_agents = sum(1 for *_, t, _ in traders if t == "agent")
    n_appr = sum(1 for *_, a in traders if a)
    logger.info(
        "classified %d agent(s) (%d approved/open-source) and %d human(s) "
        "(%d valid agent(s), %d approved digest(s))",
        n_agents, n_appr, len(traders) - n_agents, len(valid_agents), len(approved_digests),
    )

    # USD value of the round's emission, to size the human pool's dollar cap. Best-effort:
    # None -> the human cap is 0 this round (humans earn nothing, agents absorb), never a crash.
    e_usd = compute_emission_usd(subtensor, netuid)
    if e_usd is None:
        logger.warning("emission USD unavailable -- human cap -> 0 this round (agents absorb the share)")

    # Open-source phase latch: begun iff the top validator has approved any (open-sourced) agent.
    ever_os = bool(approved_digests)

    rewards = calculate_rewards([(hl, t, o) for _, hl, t, o in traders], e_usd, ever_os)

    weights: dict[int, float] = {}
    if len(rewards) != len(traders):
        logger.error("calculate_rewards returned %d weights for %d traders -- burning the round",
                     len(rewards), len(traders))
    else:
        for (uid, *_), r in zip(traders, rewards):
            if r > 0:
                weights[uid] = weights.get(uid, 0.0) + r

    # Burn whatever the tournament did not award (human shortfall with no agents, ineligible
    # field, missing data/price, ...). Weights then sum to ~1.0.
    assigned = sum(weights.values())
    burn = 1.0 - assigned
    if burn > 1e-12:
        weights[BURN_UID] = weights.get(BURN_UID, 0.0) + burn
        logger.info("burning %.2f%% of emission to UID %d", burn * 100, BURN_UID)
    elif burn < -1e-9:  # assigned > 1 (shouldn't happen now the human cap is clamped) -- SDK renormalizes
        logger.warning("assigned weights exceed 1.0 by %.2e -- relying on SDK normalization", -burn)

    # Belt-and-suspenders: never set an all-zero weight vector.
    if not weights:
        weights[BURN_UID] = 1.0

    uids = sorted(weights)
    return uids, [weights[uid] for uid in uids]
