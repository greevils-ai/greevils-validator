"""Greevils subnet validator.

Every round (default: once per 24h) the validator:
  1. syncs the metagraph,
  2. reads each registered miner's on-chain ownership commitment and verifies the
     signature proves they control the Hyperliquid account they claim (commitments.py),
  3. classifies each claimed account as a *greevil agent* or a *human* trading account by
     asking greevils-api which agent accounts are live + healthy + attested (api_client.py),
  4. scores EVERYONE in one unified tournament -- agents and humans play each other -- via the
     single function you implement, `calculate_rewards` (rewards.py),
  5. splits emissions into an agent pool and a human pool by that unified score (the human pool
     sized by a dollar cap, agents absorbing the rest), burns whatever is unawarded to UID 0,
     and sets weights on-chain (evaluation.py + validator.py).

The only thing left to implement is the data layer behind `calculate_rewards` (rewards.py).
Everything else -- commitment verification, agent/human classification, the unified tournament,
the human cap + burn, weight setting, the heartbeat watchdog -- is wired up here.
"""
