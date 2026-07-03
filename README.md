# greevils-validator

The Greevils subnet validator. Each UTC day it verifies the Hyperliquid address every miner
claims, scores **everyone in one unified PvP tournament**, and sets emission weights on-chain.
Miners are autonomous **agents** or **humans**; both compete in the *same* tournament, and
emissions split into an agent pool and a dollar-capped human pool by the same score.

> **Full scoring spec:** [incentive.md](incentive.md) is the single source of truth for the
> math — eligibility, elimination, the `G × M` score, the human dollar cap, and the agent-lane
> phases. This README is the architecture + operations guide.

## Status

Implemented end-to-end: commitment verification, classification, the unified tournament, the
daily database, builder-exclusivity, emission pricing, and weight-setting. The one **external**
dependency is **greevils-api** (`api.greevils.ai`), which reports which accounts are valid agents
and serves approval lists. Until it is deployed the agent lane is inert and **every account is
classified human** — the validator still runs and scores the human lane.

## The mechanism (one round per UTC day, at 00:00 UTC)

A miner's on-chain job is tiny (`greevils commit`): register a neuron, then publish a commitment
proving they own a Hyperliquid address — `base64(hl_address(20B) ‖ signature(65B))` (the Raw
commitment field is capped at 128 bytes). The signature is an EIP-191 `personal_sign` over a
canonical message the validator rebuilds from the *committing hotkey* + embedded address, so a
copied blob fails recovery under another hotkey.

Each round the validator:

1. **Syncs** the metagraph.
2. **Reads + verifies** every miner's on-chain ownership commitment.
   → [commitments.py](greevils_validator/commitments.py)
3. **Builder pre-gate.** Every fill must be routed through the Greevils builder app — it carries a
   ~5 bps `builderFee`. A single fill with **no** fee (traded directly on HL) or a **different**
   rate (another app) is a **permanent DQ**, recorded indelibly. Forced liquidations are exempt.
   → [builder.py](greevils_validator/builder.py)
4. **Classifies** each surviving account: it's an **agent** iff greevils-api (`GET /submissions`)
   reports it `status=RUNNING, health=HEALTHY, attestation=PASS`; **everything else is a human**.
   → [api_client.py](greevils_validator/api_client.py)
5. **Scores everyone in ONE unified tournament** — agents and humans play each other pairwise on
   their shared overlap window; per-account score = `G × M` (gain × discipline multiplier) plus a
   decayed longevity bonus. A lifetime eligibility + elimination gate decides who enters.
   → [tournament.py](greevils_validator/tournament.py), [scoring.py](greevils_validator/scoring.py)
6. **Splits emissions** into a human pool and an agent pool by the same unified score:
   `human_share = min(HUMAN_SHARE_CAP, HUMAN_PNL_K · human_PnL$ / emission_USD)`, and
   `agent_share = 1 − human_share`. The human pool is dollar-tethered (a net-losing human lane
   funds nothing); agents absorb the rest.
   → [tournament.py](greevils_validator/tournament.py), [pricing.py](greevils_validator/pricing.py)
7. **Burns** whatever is unawarded (no eligible agents, missing price feed, a lone miner, …) to
   `BURN_UID`, and **sets weights** on-chain.
   → [evaluation.py](greevils_validator/evaluation.py), [validator.py](validator.py)

### Agents, humans & approval (gva1)

Classification is **valid-or-not**, not approved-or-not: a *valid* agent is an agent even with
closed source. **Approval** governs only the agent lane's phase:

- **Grace** — no agent approved yet: every *valid* agent earns, with **no eligibility gate** (just
  survive elimination and trade ≥ 1 day).
- **Open** — the highest-staked validator has approved ≥ 1 open-sourced agent on-chain: **only
  approved agents earn**, under the full **60-day** eligibility gate; every other agent gets 0.

Approval = the top validator manually reviewed an agent's now-public code and committed its image
digest hash on-chain (`gva1:<base64(sha256(list))>`); the validator fetches that hotkey's list from
greevils-api and honors it only if the hash matches the on-chain commitment. Humans are never
phase-gated. → [approvals.py](greevils_validator/approvals.py). See
[incentive.md](incentive.md) §7 for the full latch semantics.

### The daily database (source of truth)

The validator scores from **its own SQLite db**, never a live re-fetch: one exact row per address
per UTC day, snapshotted from the Hyperliquid API at the 00:00 UTC boundary — equity (clearinghouse
account value), `net_flow` (derived as a residual), executed volume, and the coins traded (for the
spot / off-whitelist DQ). Days it couldn't snapshot live are **backfilled** by reconstruction from
HL's portfolio endpoint. Permanent DQs live here too, and are indelible.
→ [db.py](greevils_validator/db.py), [hl_data.py](greevils_validator/hl_data.py)

## Run

```bash
cd greevils-validator
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

./.venv/bin/python validator.py \
  --network finney --netuid 1 --coldkey my-wallet --hotkey my-hotkey

./.venv/bin/python validator.py --once     # single round then exit (testing)
```

The validator hotkey must be **registered on the subnet** (it sets weights). The first round runs
immediately at boot; **scoring** then fires at **00:00 UTC** each day, while **weights are re-set every
epoch** (tempo) in between — so the validator's `last_update` stays within `activity_cutoff` and its
weights keep counting in consensus rather than going stale for hours between daily rounds. A heartbeat watchdog re-execs the
process if a round hangs past `HEARTBEAT_TIMEOUT`. CLI flags (`--network --netuid --coldkey
--hotkey --api --once --log-level`) are each backed by env (`NETWORK NETUID WALLET_NAME
HOTKEY_NAME LOG_LEVEL`).

## Deployment checklist (don't skip)

- **`GREEVILS_DB` must be an absolute path on persistent storage.** The default
  (`greevils_validator.sqlite`) is *relative* — on an ephemeral filesystem or from a changed working
  directory, a restart silently opens a fresh empty db. Equity/flow re-backfill from HL, but the
  **indelible DQ table is lost and unrecoverable** (a permanently-DQ'd account could re-enter).
- **Score at 00:00 UTC; set weights every epoch.** The loop self-schedules the daily **scoring** round
  to the 00:00 boundary (that timing is what makes each day's equity snapshot exact), and re-sets the
  cached weight vector every **epoch** in between so `last_update` stays within `activity_cutoff`. A
  scoring round forced far off-boundary writes that day's equity as the *current* value (`net_flow` is
  protected — forced to 0). `tempo` is read from chain; `EPOCH_TEMPO_FALLBACK` (360) is the fallback.
- **greevils-api must be reachable** for the agent lane to function; otherwise every account is a
  human and the agent pool burns.

## Config

Wallet / network / netuid are CLI options (above). Everything else is env-driven policy — see
[greevils_validator/config.py](greevils_validator/config.py):

| Env | Default | Meaning |
|---|---|---|
| `GREEVILS_API` | `https://api.greevils.ai` | greevils-api base URL (valid agents + approval lists) |
| `GREEVILS_DB` | `greevils_validator.sqlite` | daily SQLite db path — **set to an absolute, persistent path** |
| `BURN_UID` | `0` | UID that un-awarded emission is burned to |
| `HUMAN_SHARE_CAP` | `0.50` | hard ceiling on the human pool (clamped to [0,1]) |
| `HUMAN_PNL_K` | `0.1` | human emission-$ ≤ `k` × human realized PnL-$ |
| `PNL_WINDOW_DAYS` | `1` | horizon for both human PnL and emission |
| `TAO_USD_URL` / `TAO_USD_FALLBACK` | CoinGecko / `0` | TAO→USD for the human cap; failure → cap 0, never a crash |
| `EMISSION_ALPHA_PER_ROUND` / `ALPHA_TAO_PRICE` | `0` / `0` | manual overrides if SDK reads are unavailable (0 → read on-chain) |
| `BUILDER_FEE_BPS` / `BUILDER_FEE_TOL_BPS` | `5` / `0.5` | builder-fee rate + match tolerance (bps) |
| `BUILDER_MIN_NOTIONAL` | `1` | $ floor; skip dust fills (fee may round to 0) |
| `BUILDER_EXCLUSIVITY_START` | (unset) | rule's effective date; earlier history is exempt (unset → entire history) |
| `HEARTBEAT_TIMEOUT` | `600` | watchdog restart threshold (seconds) |

## Layout

```
validator.py                       entrypoint: CLI, heartbeat, 00:00-UTC loop, set_weights
greevils_validator/config.py       policy: cadence, emission split, builder, API endpoint (env-driven)
greevils_validator/commitments.py  read + verify on-chain Hyperliquid ownership claims (eth_account)
greevils_validator/builder.py      builder-exclusivity pre-gate (real-time builderFee check)
greevils_validator/api_client.py   query greevils-api for valid agent accounts (+ image digests)
greevils_validator/approvals.py    top validator's gva1 approval hash -> fetch + verify list from api
greevils_validator/hl_data.py      Hyperliquid fetch + portfolio-based backfill reconstruction
greevils_validator/db.py           the daily SQLite db (source of truth) + indelible DQs
greevils_validator/scoring.py      per-account factor engine: returns, drawdown, U -> G, M
greevils_validator/tournament.py   eligibility + elimination gate, unified PvP, pool split
greevils_validator/rewards.py      calculate_rewards: run the tournament -> one weight per trader
greevils_validator/pricing.py      emission USD value (alpha emission x alpha->TAO x TAO->USD)
greevils_validator/evaluation.py   one round: classify, score, burn the remainder, build weights
incentive.md                       the full scoring / eligibility spec (single source of truth)
```
