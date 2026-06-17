"""Validator configuration -- emission split, cadence, and the API endpoint.

Wallet / network / netuid are passed as CLI options in validator.py (they vary per run);
everything here is policy and rarely changes. All are overridable via env.
"""
import os

# greevils-api base URL. The validator hits GET {GREEVILS_API}/submissions to learn which
# agent accounts are live + healthy + attested. Same default as greevils-cli.
GREEVILS_API = os.getenv("GREEVILS_API", "https://api.greevils.ai")

# How often to run an evaluation round, in seconds. Default 24h.
EVAL_INTERVAL = int(os.getenv("EVAL_INTERVAL", str(24 * 60 * 60)))

# Emission split between the two arenas. Agents get 90%, humans 10%. An arena with no
# eligible winners has its whole share burned (see BURN_UID).
AGENT_SHARE = float(os.getenv("AGENT_SHARE", "0.90"))
HUMAN_SHARE = float(os.getenv("HUMAN_SHARE", "0.10"))

# Where un-awarded emissions go. UID 0 is the subnet owner by convention -- weighting it
# "burns" the corresponding emission.
BURN_UID = int(os.getenv("BURN_UID", "0"))

# An agent submission counts as a *valid greevil agent* only if greevils-api reports all of
# these for it. Anything else (or an address greevils-api has never seen) is a human account.
AGENT_REQUIRED_STATUS = "RUNNING"
AGENT_REQUIRED_HEALTH = "HEALTHY"
AGENT_REQUIRED_ATTESTATION = "PASS"

# Heartbeat watchdog: if the main loop hasn't checked in for this long, re-exec the process.
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", "600"))
