"""Greevils subnet validator.

Every round (once per UTC day, at 00:00 UTC) the validator:
  1. syncs the metagraph,
  2. reads each registered miner's on-chain ownership commitment and verifies the
     signature proves they control the Hyperliquid address they claim (commitments.py),
  3. classifies each claimed address as a *greevil agent* or a *human* by asking
     greevils-api which agent addresses are live + healthy + attested (api_client.py),
  4. scores EVERYONE in one unified tournament -- agents and humans play each other --
     via `calculate_rewards` (rewards.py),
  5. splits emissions into an agent pool and a human pool by that unified score (the human pool
     sized by a dollar cap, agents absorbing the rest), burns whatever is unawarded to UID 0,
     and sets weights on-chain (evaluation.py + validator.py).

Everything is implemented -- commitment verification, agent/human classification, the unified
tournament, the daily database, the human cap + burn, weight setting, the heartbeat watchdog.
The one external dependency is greevils-api; until it is reachable, every address is scored as a
human (the agent lane burns).
"""
