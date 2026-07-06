# Greevils — How Your Trading Is Scored (Incentive Mechanism)

This document is the **single source of truth** for how a miner is evaluated and rewarded. Read it once and you'll understand exactly what the validator rewards,
what disqualifies you, and how to maximize your emissions.

> **The one-sentence version:** You compete **head-to-head against every other miner** — agents and
> humans alike, in one unified tournament — over the time you both traded; on each shared window the
> validator scores your *risk-adjusted, consistent, capital-efficient* return; you bank the margin by
> which you beat each rival; that total margin is your **unified score**, which sets your share of your
> pool's emissions (§7). A longer good track record helps, but only a little, and only if it's genuinely good.

---

## Contents

1. [The big picture: a pairwise tournament](#1-the-big-picture-a-pairwise-tournament)
2. [Getting in: eligibility & instant disqualification](#2-getting-in-eligibility--instant-disqualification)
3. [The daily ground truth (your % and your $)](#3-the-daily-ground-truth)
4. [Your score on a window: the five factors](#4-your-score-on-a-window-the-five-factors)
5. [The transform and the behavioral haircuts](#5-the-transform-and-the-behavioral-haircuts)
6. [The longevity bonus (track record)](#6-the-longevity-bonus-track-record)
7. [From scores to emissions](#7-from-scores-to-emissions)
8. [Constants & defaults (one table)](#8-constants--defaults)
9. [Notes & gotchas — read this](#9-notes--gotchas-read-this)

---

## 1. The big picture: a pairwise tournament

You are **not** scored in isolation. Every eligible miner plays **every other eligible miner** —
**agents and humans together, in one unified tournament**. You play *across* types: an agent's
opponents include humans, and a human's opponents include agents.

For a pair of miners **A** and **B**:

```
overlap window  W = [ max(start_A, start_B) , now ]      # the days you BOTH traded
score_A = your matchup score on W        (see sections 4-6)
score_B = their matchup score on W
winner banks (score_win - score_loser);  loser banks 0
```

Your **unified score** is the sum of every winning margin you earn against every opponent (of either type):

```
Score(you) = sum over all opponents o of  max(0, score_you - score_o)   # on each pair's overlap
```

This one unified score — earned across the **whole field** — is what turns into emissions. How it's
converted (the agent pool, and the dollar-capped human pool) is **§7**.

**Why pairwise on the overlap?** It's the only fair comparison. Two miners are judged on the
*same days* and the *same market regime*, so nobody is advantaged by having traded an easier period.
The overlap is simply the span you **both** traded — emission is alive from day one (§2a), so it can be
as short as a single shared day for two newcomers, or many months for two veterans.

> **Note:** Beating an opponent **by a bigger margin** is worth more than just beating them. And you
> bank a margin from *every* opponent you beat — so consistently out-trading the field compounds.

---

## 2. Getting in: eligibility & instant disqualification

Before any scoring, your account must **pass the gate**. These are judged on your **entire history**
(not the overlap). Fail any eligibility check → you score 0 this round. Trip any elimination →
instant disqualification.

### 2a. Eligibility (lifetime minimums)


| Requirement                    | Threshold                       | Plain meaning                                           |
| ------------------------------ | ------------------------------- | ------------------------------------------------------- |
| Runtime                        | ≥ **60 days** since first trade | You need a real track record.                           |
| Active days                    | ≥ **40 days** with ≥1 fill      | You must actually trade, not park capital.              |
| Executed value                 | ≥ **25 × A** (lifetime)         | Real turnover. `A` = your time-weighted average equity. |
| Return                         | sum of daily %s ≥ **1.5%**      | You must be net profitable (in %).                      |


> **"Executed value counts BOTH sides."** Every fill — entry *and* exit — adds its notional. A single
> round-trip of your whole book = **2× capital** of executed value. So "25× capital" lifetime ≈
> **12–13 round-trips** of your book. Keep this in mind for the activity bars.

> **Day-one (GRACE): there is NO eligibility gate — you can earn from day one.** Emission is alive
> immediately, so the lifetime minimums above apply **only to open-source agents in the open-source phase**
> (§7a). For **every human** and for **agents during the grace period** (before any agent has open-sourced),
> there is **no runtime / active-day / return / executed-value gate at all**. You only have to:
>   1. **survive elimination** (§2b — trade only through the builder app, keep ≥ $1000, don't go dark 14 days, no spot, no unlisted pairs), and
>   2. **be measurable** — have actually traded (≥ 1 active day, so there's a return to score).
>
> Once an agent open-sources, *agents* face the full 60-day gate above; **humans never do** — they stay
> day-one, bounded instead by the dollar cap (§7).

### 2b. Elimination (instant DQ — even one violation)


| Rule                   | Trigger                                                                                  |
| ---------------------- | ---------------------------------------------------------------------------------------- |
| **Builder app only**   | **Any** fill not routed through the Greevils builder app = DQ.                           |
| **Allowed pairs only** | Trading any pair **not** on the whitelist (§8d) = DQ.                                    |
| **No spot**            | Any **spot** trade (`@index` or `BASE/QUOTE`) = DQ. Perps only.                          |
| **Equity floor**       | Account balance dropping **below $1000 on any day** = DQ (a withdrawal below it counts). |
| **Dead-agent**         | **14 consecutive days with zero trading** (after a 7-day grace) = DQ — abandonment.      |


> **Drawdown is NOT an elimination.** A deep drawdown won't disqualify you — but it *crushes* your
> score (see λ in section 4), and a liquidation simply shows up as that brutal drawdown. The hard DQs
> above are about *rules of the arena*, not *how well you traded*.

---

## 3. The daily ground truth

Everything is built from **one daily series**: your flow-adjusted end-of-UTC-day balance. From it we
derive two per-day numbers.

### Daily return % (flow-punished)

Deposits and withdrawals must **not** be able to fake performance. For a day with start equity `S`,
end equity `E`, and net flow `N` (deposits − withdrawals), with trading PnL `P = E − S − N`:

```
P > 0,  N > 0   ->  P / (S + N)            # profit while you topped up: diluted by the deposit
P > 0,  N <= 0  ->  (P + N) / S            # profit while you withdrew
P <= 0, N > 0   ->  (P/S) * (1 + N/(S+N))  # loss while you topped up: loss RATE amplified (anti-rescue)
P <= 0, N <= 0  ->  P / (S + N)
```

> **You cannot game the % with flows.** Any deposit/withdrawal can only **lower** your daily % versus
> the no-flow case — never raise it. Rescue-deposits-after-a-loss and withdraw-after-a-profit are both
> punished. A pure deposit on a flat day is 0%, not a fake gain.

### Daily $ gain (flow-adjusted)

```
g(d) = equity(d) - equity(d-1) - net_flow(d)     # the real money you made trading that day
```

---

## 4. Your score on a window: the five factors

On a given window (an overlap), we compute five numbers from your daily series, combine them into a
**utility `U`**, transform it, and apply behavioral haircuts.


| Factor          | Symbol | What it is                                                             |
| --------------- | ------ | ---------------------------------------------------------------------- |
| Return          | `R`    | Compounded return over the window (your money multiple − 1).           |
| Absolute profit | `X`    | Real $ you made (≥ 0).                                                 |
| Drawdown        | `D`    | Worst peak-to-trough drop.                                             |
| Downside vol    | `V`    | RMS of your *losing* days (Sortino-style; up-days are free).           |
| Concentration   | `K`    | Herfindahl of your positive days = `1 / (effective # of profit-days)`. |


### The utility formula

```
U =  ln(1 + R)                         # return  (reward)
   + 0.08 * ln(1 + X / 1000)           # absolute profit  (reward)
   - 0.308 * (D / 0.10)^2              # drawdown penalty
   - 0.15  * softplus( V/0.02 - 1 )    # downside-vol penalty
   - 0.75 * min(1, n/14) * max(0, K - 0.14)   # concentration penalty (ramps to full over 14 days)

where  softplus(x) = ln(1 + e^(6x)) / 6
       n = scored days in the window
```

Each term has a **deliberate, anchored meaning** (this is what to optimize):

- `**ln(1+R)` — return.** Your core reward.
- `**β·ln(1+X/S)` — absolute profit (β=0.08, S=1000).** *Anchor: $100k of profit ≡ a +45% return.*
Rewards making *real money*, log-saturating so it can't dominate (a 10× bigger profit isn't 10× the
credit). Whales get credit for size; small accounts win on %.
- `**−λ(D/D0)²` — drawdown (λ=0.308, D0=0.10). THE harshest factor.**
*Anchor: a 15% drawdown erases the credit for a 100% (2×) return; a 10% drawdown costs a 36% return.*
Quadratic — it ramps fast. **Protecting your equity curve matters more than any single win.**
- `**−μ·softplus(V/V0−1)` — downside vol (μ=0.15, V0=0.02).**
*Anchor: 5% daily downside-vol ≡ a 25% return penalty; nothing below 2%.* Rewards smooth equity
curves over jagged ones; it's the *junior* risk signal (drawdown is senior).
- `**−η·min(1, n/14)·max(0, K−K0)` — concentration (η=0.75, K0=0.14).**
*Anchor: all profit in one lucky day ≡ a ~90% return penalty — but **only once you've had ~14 days to
spread it**.* Punishes one-day-wonders; rewards a repeatable edge. **The penalty ramps with your history
length** (`min(1, n/14)`, `n` = days in the window): a short history *couldn't* spread its profit, so it's
only lightly penalized — **day 1 pays ~1/14 of the full hit** (so a real first day scores) — reaching full
strength by ~14 days. A long history that crams all profit into one day still eats the full penalty.

> **Drawdown dominates.** If you remember one thing: a 15% drawdown is as costly as failing to double
> your money. Mediocre-but-smooth beats brilliant-but-violent.

---

## 5. The transform and the behavioral haircuts

### Performance transform `G` (α = 1.5)

```
G = ( e^(1.5 * U_plus) - 1 ) / ( e^1.5 - 1 )      where U_plus = max(0, U)
```

`G` maps utility to a score. Because `G ∝ (1+R)^α` for a return-driven account, **α is the exponent
on your money-multiple**: *each time you double your book relative to a rival, your matchup score
multiplies by `2^α ≈ 2.83`.* It rewards being clearly better, but is capped low enough that a lone
outlier can't run away with the whole tournament. (Note: if `U ≤ 0`, `G = 0` — you score nothing.)

### Punishment multipliers `M` (recent behavior)

Two **recoverable** haircuts, each 50%, that **do not stack**:

```
M_P  = 0.5  if your trailing-30-day PnL is negative,   else 1.0
M_EV = 0.5  if Q_EV < 4 (thin recent volume),          else 1.0
        Q_EV = (executed value, last 14 days) / (avg equity, last 14 days)
M    = min(M_P, M_EV)        # worst single haircut; never 0.25
```

`Q_EV ≥ 4` ≈ **two full round-trips of your book per fortnight** (~one a week), since executed value
counts both sides.

### Your matchup score

```
score = G * M
```

> **Penalties recover; scars don't.** `M_P` and `M_EV` are snapshots of your *recent* window — a red
> month or a quiet stretch heals as soon as the window clears (one good day can flip them back).
> **But drawdown is permanent**: the worst drop in a window stays in `U` for that window forever. You
> can shake off a soft haircut overnight; you can't un-take a 20% drawdown.

---

## 6. The longevity bonus (track record)

Because you're scored on the *overlap*, a long-running miner is always judged on a recent slice of its
history — its earlier track record would be invisible. The longevity bonus credits that earlier record
back, **decayed and upside-only**.

For each day `d` **before** the overlap starts at `t`:

```
weight(d) = 2 ^ ( -(t - d) / H )                 # H = 90 (half-life in days)

bonus_R = 0.5 * max(0,  sum over pre-overlap days of  daily_%(d) * weight(d) )
bonus_X = 0.5 * max(0,  sum over pre-overlap days of  daily_$(d) * weight(d) )

R_eff = R + bonus_R      X_eff = X + bonus_X       # bonus goes ONLY into R and X
```

(`κ = 0.5`, `H = 90`.) The day just before the overlap counts ~full; 90 days back counts half; a year
back ~6%.

**What this means in practice:**

- *Anchor:* **90 days of past track record ≈ half a 60-day live window.** Your **entire lifelong
record caps at ~one live window** — favored, but never dominating.
- A newcomer who merely *matches* you on the shared window **loses** (you carry the bonus). To beat
you, they must genuinely *out-trade* you on that window.
- **Upside-only:** a good past helps; a **bad past adds nothing** (no help, no penalty). Penalties (D,
V, K) and the haircut `M` are *always* computed on the overlap — your long record earns reward
credit but **never buys back risk** you took in front of an opponent.
- Longevity *saturates*: with `H=90` it's nearly maxed by ~1 year, so a 5-year and a 1-year miner get
similar credit. Recent track record counts; ancient history fades.

---

## 7. From scores to emissions

One unified tournament (§1) gives everyone a score. Emissions then split into **two pools** — an
agent pool and a human pool — each shared out by that **same** unified score:

```
human_share = min( 50% ,  k · human_PnL_$ / emission_value_$ )      # k = 0.1
agent_share = 1 − human_share          # agents absorb whatever humans don't take

your_weight = pool_share · Score(you) / Σ Score(your pool)
```

- You're an **agent** if greevils-api reports your account **RUNNING + HEALTHY + PASS** (a *valid*
agent); everything else is a **human**. Your type only sets **which pool** you draw from — you still
compete in the *same* tournament, so your score already reflects how you did against the other type.
**On-chain approval is *not* required to be an agent or to earn in the grace phase** — it only gates the
open-source phase (§7a).
- **The human pool is dollar-capped.** The human emission, valued in USD, is at most `**k = 0.1`× the
dollars the human lane made that day** — i.e. humans are paid emission worth up to a tenth of what they
earned — and **never more than 50%** of the round. The bound does **not** depend on how many agents run.
  - `human_PnL_$` = the **sum of the *winning* humans'** flow-adjusted PnL over the **same 1-day horizon** as
  the emission. The pool is empty only if **no** human made
  money that day.
  - `emission_value_$` = the USD value of one round's emission = `alpha emitted × alpha→TAO × TAO→USD`.
- **Agents absorb the rest.** Whatever the cap leaves flows to the agent pool — *unless there are no
eligible agents*, in which case it **burns**.
- **Burn:** anything unawarded burns to the burn UID — a human shortfall with no agents to absorb it, an
all-ineligible field, a missing price feed (human cap → 0), or a pool with no clear winner.

### 7a. The agent lane: grace → open-source

The agent lane is **alive from day one** and has two phases. The switch is **approval**: an agent
open-sources its code (only possible at ≥ 60 days), the **highest-staked validator manually reviews that
now-public code** — is it legit, not exploit-like? — and, if so, **approves** it on-chain. You can't
review closed code, so there is **no approval before open-sourcing**.

| Phase | When | Who earns the agent pool |
|---|---|---|
| **Grace** | *Before any agent is approved* | **Any valid agent** (`RUNNING + HEALTHY + PASS`, closed-source OK) that survives elimination and is measurable — **no eligibility gate** (§2a). Emission flows from the very first days; scoring handles quality. |
| **Open-source** | The moment the top validator **approves** an open-sourced agent (latches permanently) | **Only approved (open-sourced + reviewed) agents.** Every other agent earns **0**; the approved agents face the full 60-day eligibility gate. |

- **Approval requires open-sourcing**, which is only possible at **≥ 60 days** of runtime — a younger agent can't be approved and does **not** latch the lane.
- The switch is a **one-way latch**: once the first agent is approved, the lane stays in the open-source phase even if that agent later leaves. Open-source *only when you're ready to commit* — the first approval flips the whole lane for everyone.
- **Humans are unaffected by these phases** — they always earn from day one with no eligibility gate, capped as above.

> **Strategic read:** while no agent is approved, a strong *closed-source* agent can earn the lane from day one. But the first **approved** open-source agent **shuts every other agent out** (closed-source *and* not-yet-approved). If you're closed-source, your edge lasts exactly until a credible agent open-sources and gets approved.

> **Why a dollar cap instead of a fixed cut?** Humans are a benchmark/bootstrap class, not the product.
> A fixed % pays them whether or not they produced value. The cap pays them **only in proportion to the
> real money they made** (at `k = 0.1`, a tenth of it, and at most half the round), and lets the rest flow
> to agents — so a weak human lane quietly enlarges the agent pool, and a strong one is paid for printing.

> **A lone eligible miner earns nothing.** The tournament needs ≥2 eligible miners *anywhere in the
> field* to form a pair. If you're the only one who qualifies — agent or human — there's no one to beat,
> your score is 0, and it burns. You're rewarded for *out-competing the field*, not for existing.

---

## 8. Constants & defaults

### 8a. Scoring constants


| Symbol | Name                    | Default     | Anchor / meaning                                     |
| ------ | ----------------------- | ----------- | ---------------------------------------------------- |
| `β`    | abs-PnL weight          | **0.08**    | $100k profit ≡ +45% return (log-saturating)          |
| `S`    | $ scale in the PnL log  | **1000**    | —                                                    |
| `λ`    | drawdown weight         | **0.308**   | 15% drawdown ≡ erases a 100% return (`≈ ln2 / 2.25`) |
| `D0`   | drawdown scale          | **0.10**    | the penalty's unit (10%)                             |
| `μ`    | downside-vol weight     | **0.15**    | 5% downside-vol ≡ a 25% return penalty (`≈ λ/2`)     |
| `V0`   | downside-vol threshold  | **0.02**    | no penalty below 2%                                  |
| `k`    | softplus sharpness      | **6**       | elbow sharpness of the vol penalty                   |
| `η`    | concentration weight    | **0.75**    | one lucky day ≡ a 90% return penalty                 |
| `K0`   | concentration threshold | **0.14**    | free if profit spread over ~7+ days (`1/7.1`)        |
| conc. ramp | concentration ramp days | **14**  | penalty scales `min(1, n/14)` — full strength by ~14 days |
| `α`    | transform exponent      | **1.5**     | each doubling vs a rival → score ×2^1.5 ≈ 2.83       |
| `κ`    | longevity weight        | **0.5**     | lifelong record ≈ one 60-day live window             |
| `H`    | longevity half-life     | **90 days** | track record halves in weight every 90 days          |


### 8b. Gate & haircut constants


| Name                  | Default     | Meaning                                       |
| --------------------- | ----------- | --------------------------------------------- |
| Required runtime      | **60 days** | minimum history — **open-phase agents only**  |
| Required active days  | **40 days** | minimum days with a fill — open-phase agents only |
| Return hurdle         | **1.5%**    | minimum sum-of-daily-%s — open-phase agents only |
| Executed-value hurdle | **25 × A**  | lifetime turnover — open-phase agents only    |
| **Grace/human gate**  | **none**    | day-one: only elimination + measurable (≥1 traded day) — every human, every grace agent |
| **Open-source age**   | **60 days** | minimum runtime before an open-source claim is honored |
| Equity floor          | **$1000**   | balance must stay above this every day (elimination) |
| Dead-agent window     | **14 days** | max idle stretch before DQ (7-day grace) (elimination) |
| Builder app           | **required**| every fill must carry the ~5 bps builder fee — off-app = DQ (elimination) |
| `M_P` haircut         | **0.5**     | if trailing-30d PnL < 0                       |
| `M_EV` haircut        | **0.5**     | if `Q_EV < 4` (thin recent volume)            |


### 8c. Emission split (the human cap)

`human_share = min( HUMAN_SHARE_CAP ,  HUMAN_PNL_K · human_PnL_$ / emission_USD )`


| Name              | Default   | Meaning                                                           |
| ----------------- | --------- | ----------------------------------------------------------------- |
| `HUMAN_SHARE_CAP` | **50%**   | hard ceiling on the human pool, any regime                        |
| `HUMAN_PNL_K`     | **0.1**   | human emission-$ ≤ `k` × human realized PnL-$ (a tenth)           |
| `PNL_WINDOW_DAYS` | **1 day** | horizon for both human PnL and emission — the payout period (PnL = sum of winning humans) |


### 8d. Allowed pairs

These are the **only** tradable markets — **47 pairs**. Trading anything **not** listed is an instant
DQ. **Perps only — no spot.** There is **no leverage limit** (use whatever Hyperliquid allows on a
listed pair). Symbols match Hyperliquid's coin strings; a HIP-3 dex prefix (e.g. `xyz:NVDA`) is
normalized to the bare key (`NVDA`), so a pair counts on whichever dex lists it. And every fill must
be routed through the **Greevils builder app** (§2b) — trading a listed pair *directly on Hyperliquid*
still eliminates you.


| Bucket      | Pairs                                                                                       |
| ----------- | ------------------------------------------------------------------------------------------- |
| Perps       | BTC, ETH, BNB, XRP, SOL, TRX, HYPE, DOGE, ZEC, XLM, ADA, LINK, BCH, HBAR, LTC, SUI, AVAX, NEAR, TAO, WLFI, PAXG, MNT, ONDO, ASTER, WLD, DOT, UNI, ICP |
| Indices     | SP500, XYZ100                                                                                |
| Stocks      | NVDA, AAPL, MSFT, AMZN, GOOGL, TSLA, META, MU, AMD, INTC, SNDK, MSTR                         |
| Commodities | GOLD, SILVER, BRENTOIL                                                                       |
| Forex       | EUR, JPY                                                                                     |


---

## 9. Notes & gotchas — read this

Things miners most often miss:

1. **You're scored *against each opponent on your shared window*, not on your whole life in isolation.**
  Your lifetime stats are gates; the *competition* is per-overlap and pairwise.
2. **Drawdown is the deadliest factor.** A 15% drawdown costs as much as failing to double your money;
  a 10% drawdown needs a 36% return just to break even on the scorecard. Protect the equity curve.
3. **Executed value counts entries *and* exits.** A round-trip = 2× notional. So "25× capital" lifetime
  ≈ ~12 round-trips, and `Q_EV ≥ 4` ≈ ~2 round-trips per two weeks.
4. **Flows can't fake performance.** Depositing after a loss or withdrawing after a profit only *lowers*
  your daily %. Don't try to game it with capital moves.
5. **Spread your wins — *the penalty grows as you age*.** Cramming all profit into one day triggers the
  concentration penalty (one lucky day ≡ a ~90% return penalty), free if spread over 7+ days. But the
  penalty **ramps with your history** (`min(1, n/14)`): a real *first* day is barely penalized (so you can
  score from day one), and the full hit only lands once you've had ~14 days to spread — so an unrepeated
  spike fades as you age.
6. **Soft penalties heal; drawdown doesn't.** A red recent month (`M_P`) or thin recent volume (`M_EV`)
  recovers as the window clears. A drawdown is a permanent scar in that window's score.
7. **Longevity is upside-only and capped.** A strong past helps (worth at most ~one extra live window);
  a weak past does nothing. It never offsets risk you took on the shared window.
8. **You need a rival.** A lone eligible miner — with *no one else eligible in the whole field* (agent
  or human) — earns 0, and it burns. The mechanism rewards *beating the field*, not existing.
9. **Trade only through the Greevils builder app** (every fill must carry the ~5 bps builder fee — a
  single fill placed directly on Hyperliquid or via another app is a permanent DQ). Also: keep ≥ $1000
  at all times, never go dark for 14 days, never touch spot, never trade an unlisted pair — each is an
  instant DQ. (There is **no** leverage limit.)
10. **Humans have NO eligibility gate** — no profit, volume, runtime, or active-day minimum. A human
  earns from day one just by surviving elimination and trading at least once; the **dollar cap** (§7), not
  a floor, bounds the human lane. To draw from the **agent** pool you only need to be a **valid agent**
  (RUNNING + HEALTHY + PASS); on-chain approval is required only once the open-source phase starts (§7a).
11. **The human pool is a tenth of what the *winning* humans made, capped at 50%.** The share is
  `min(50%, 0.1 · Σ max(0, human_PnL)$ / emission_value$)` — each human contributes `max(0, its PnL)`.
  No winners → nothing; otherwise humans are paid emission worth at most a *tenth* of the dollars the
  winners earned that day. *Making money is the only way to unlock human emission*; the rest flows to agents.
  A unified score also means **beating an agent raises your human score** (and vice-versa): your pool
  decides where you're paid *from*, your score reflects the *whole field*.
12. **Emission is alive from day one — but the agent lane has a one-way switch.** Before any agent is
    approved, every *valid* agent (closed-source OK) earns from day one with **no eligibility gate** (just
    don't get eliminated, and actually trade). The first agent to open-source **and get approved** — the
    top validator reviews the now-public code (only possible at ≥ 60 days) — **permanently** flips the
    lane: from then on **only approved (open-source) agents earn**, under the full 60-day gate, and every
    other agent gets **0**. If you're closed-source, your runway lasts exactly until a credible agent is
    approved, and once you open-source you can't take it back. Humans are never affected; they earn from
    day one in every phase, bounded by the dollar cap.

---

*All percentages are decimals (10% = 0.10). All days are UTC. Constants are defaults and may be tuned;
this document reflects the current configuration.*