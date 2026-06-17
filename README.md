# greevils-validator

The Greevils subnet validator. It scores miners on the Hyperliquid accounts they claim and
sets weights on-chain. **The only thing left to implement is one function** —
`calculate_rewards` in [greevils_validator/rewards.py](greevils_validator/rewards.py).

## The mechanism

A miner's job is tiny (done with `greevils commit`): register a neuron, then publish an
on-chain commitment proving they own a Hyperliquid account — an `{hl_address, message,
signature}` blob where `signature` is an EIP-191 `personal_sign` over `message` by that
account's key.

Every round (default **every 24h**) the validator:

1. **Syncs** the metagraph.
2. **Reads commitments** for all registered miners (`get_all_commitments`) and **verifies**
   each one: the signature must recover the claimed `hl_address`, and the signed message must
   reference the committing hotkey (so a commitment can't be stolen and replayed).
   → [commitments.py](greevils_validator/commitments.py)
3. **Classifies** each claimed account. It queries greevils-api (`GET /submissions`) for the
   live agent accounts; an address is a **valid agent** iff some submission has that
   `agent_address` with `status=RUNNING`, `health=HEALTHY` and `attestation=PASS`. Every
   other claimed address (including ones the API has never seen) is a **human** account.
   → [api_client.py](greevils_validator/api_client.py)
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
greevils_validator/api_client.py   query greevils-api for valid agent accounts
greevils_validator/rewards.py      calculate_rewards  <-- IMPLEMENT THIS
greevils_validator/evaluation.py   classify into arenas, apply 90/10 split + burn, build weights
```
