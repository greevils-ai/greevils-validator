# greevils-validator

The Greevils subnet validator. It scores miners on the Hyperliquid accounts they claim and
sets weights on-chain. **The only thing left to implement is one function** —
`calculate_rewards` in [greevils_validator/rewards.py](greevils_validator/rewards.py).

## The mechanism

A miner's job is tiny (done with `greevils commit`): register a neuron, then publish an
on-chain commitment proving they own a Hyperliquid account — a compact
`base64(hl_address(20B) ‖ signature(65B))` blob (the Raw commitment field is capped at 128
bytes). `signature` is an EIP-191 `personal_sign` over the canonical message; the message
isn't stored, the validator rebuilds it from the committing hotkey + address.

Every round (default **every 24h**) the validator:

1. **Syncs** the metagraph.
2. **Reads commitments** for all registered miners (`get_all_commitments`) and **verifies**
   each one: it rebuilds the canonical message from the committing hotkey + the embedded
   address and checks the signature recovers that address. Rebuilding from the *committing*
   hotkey is what binds the claim — a copied blob fails recovery under another hotkey.
   → [commitments.py](greevils_validator/commitments.py)
3. **Classifies** each claimed account into **agent** or **human**. An account is an **agent**
   only if BOTH hold:
   - greevils-api (`GET /submissions`) reports its `agent_address` as a **valid agent** —
     `status=RUNNING`, `health=HEALTHY`, `attestation=PASS`
     → [api_client.py](greevils_validator/api_client.py); and
   - its image digest is **approved** — any hotkey publishes its approved-digest list to
     greevils-api (`greevils approve`) and commits the list's hash on-chain
     (`gva1:<base64(sha256(list))>`). The validator takes the **highest-staked
     validator-permit holder**, fetches its list from greevils-api (`GET /approved/{hotkey}`),
     and uses it only if the list's hash matches that hotkey's on-chain commitment. Any
     mismatch/fetch failure ⇒ nothing approved. → [approvals.py](greevils_validator/approvals.py)

   Everything else — unknown accounts, valid-but-**unapproved** agents — is a **human**.
4. **Scores** each arena independently with the same logic — agents compete against agents,
   humans against humans — by calling `calculate_rewards(addresses)`.
   → [rewards.py](greevils_validator/rewards.py)
5. **Splits emissions 90% agents / 10% humans** and sets weights. If an arena has no miners,
   or `calculate_rewards` awards nobody, that arena's whole share is **burned to UID 0**.
   → [evaluation.py](greevils_validator/evaluation.py)

## Implement `calculate_rewards`

```python
def calculate_rewards(addresses: list[str]) -> list[float]:
    # one reward per address, SAME ORDER, summing to 1.0 (or all zeros to burn this arena)
    ...
```

It's called once per arena per round. The placeholder returns all zeros (everything burns) —
the safe default until real scoring lands. Everything else is wired up; you don't touch the
commitment verification, classification, the 90/10 split, the burn, or weight-setting.

## Run

```bash
cd greevils-validator
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

./.venv/bin/python validator.py \
  --network finney --netuid 1 --coldkey my-wallet --hotkey my-hotkey

./.venv/bin/python validator.py --once     # single round then exit (testing)
```

The validator hotkey must be **registered on the subnet** (it sets weights). A heartbeat
watchdog re-execs the process if a round hangs past `HEARTBEAT_TIMEOUT`.

## Config

Wallet / network / netuid are CLI options (above). Policy is env-driven, see
[greevils_validator/config.py](greevils_validator/config.py):

| Env | Default | Meaning |
|---|---|---|
| `GREEVILS_API` | `https://api.greevils.ai` | greevils-api base URL |
| `EVAL_INTERVAL` | `86400` | seconds between rounds (24h) |
| `AGENT_SHARE` / `HUMAN_SHARE` | `0.90` / `0.10` | emission split between the arenas |
| `BURN_UID` | `0` | UID that un-awarded emission is burned to |
| `HEARTBEAT_TIMEOUT` | `600` | watchdog restart threshold (seconds) |

## Layout

```
validator.py                       entrypoint: CLI, heartbeat, 24h loop, set_weights
greevils_validator/config.py       emission split, cadence, API endpoint (env-driven)
greevils_validator/commitments.py  read + verify on-chain ownership claims (eth_account)
greevils_validator/api_client.py   query greevils-api for valid agent accounts (+ digests)
greevils_validator/approvals.py    top validator's approval hash -> fetch+verify list from api
greevils_validator/rewards.py      calculate_rewards  <-- IMPLEMENT THIS
greevils_validator/evaluation.py   classify into arenas, apply 90/10 split + burn, build weights
```
