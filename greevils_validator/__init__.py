"""Greevils subnet validator.

Every round (default: once per 24h) the validator:
  1. syncs the metagraph,
  2. reads each registered miner's on-chain ownership commitment and verifies the
     signature proves they control the Hyperliquid account they claim (commitments.py),
  3. classifies each claimed account as a *greevil agent* or a *human* trading account by
     asking greevils-api which agent accounts are live + healthy + attested (api_client.py),
  4. scores agents against agents and humans against humans -- the comparison itself is the
     single function you implement, `calculate_rewards` (rewards.py),
  5. splits emissions 90% agents / 10% humans, burning any arena that has no eligible
     winners to UID 0, and sets weights on-chain (evaluation.py + validator.py).

The only thing left to implement is `calculate_rewards` in rewards.py. Everything else --
commitment verification, agent/human classification, the emission split and burn, weight
setting, the heartbeat watchdog -- is wired up here.
"""
