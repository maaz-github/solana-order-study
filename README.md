# Inside the Block — how do Solana validators order transactions?

A measurement study for the Superteam Ukraine bounty
*"Inside the Block: How Solana Validators Order Transactions"*.

**Question:** for a given validator, by what rule does it order the transactions
inside the block it produces — what decides which transaction lands earlier and
which later, how strictly does that rule hold, and is there a rule at all?

**Answer format:** a per-validator verdict with an explicit confidence, a noise
estimate, and an honest `NO RULE DETECTED` when the data does not support one.
A null result is a valid result here.

---

## Quick start

Everything is free until the final mainnet measurement.

```bash
# 1. unit tests — no network, no keys, no cost
make test

# 2. validator discovery + client classification — read-only, no cost
make dry-run

# 3. real mainnet measurement — costs ~0.0015 SOL total
SOLANA_KEYPAIR_PATH=/keys/funder.json SOLANA_KEYPAIR_DIR=./keys make live
```

Docker Compose equivalents (`docker compose up`, `run test`, `run live`) are in
`docker-compose.yml`; the `Makefile` targets above are the fallback for hosts
without the Compose plugin. Both drive the same image.

---

## What it measures

For each selected validator we send a batch of tagged transfers inside its
leader window, then read the produced block back and record, for every one of
our own transactions:

- its index inside the block,
- the fee it paid,
- the order we sent it in,
- when it confirmed.

From that we test three candidate orderings per block:

| Hypothesis | Test |
|---|---|
| **Fee-descending** | are our transactions in the block ordered by decreasing fee? |
| **Send-order** | are they in the order we sent them? |
| **Reverse send-order** | are they in reverse send order? |

plus a Spearman rank correlation between fee and block index.

A rule is only called a rule when it holds in **≥ 80 %** of a validator's
blocks. Below that the verdict is `NO_RULE`, with the observed consistency
reported anyway so a reader can judge for themselves.

---

## Experiment design, and what it accounts for

The bounty asks the README to show how the design handles each of these.

**Leader schedule and leader types.** We read `getLeaderSchedule` and only send
into windows belonging to a selected validator. We record the raw schedule
(`schedule` in the result JSON) so a judge can re-derive which slots we acted on.

**Path from our backend to the executing node.** We send over the public RPC,
which forwards to the current leader via TPU. We do **not** connect directly to
a validator's TPU, because doing so would change the arrival distribution and
confound the measurement. This is a stated limitation, not an oversight: our
arrival order is the RPC's, not ours.

**Interaction between our own transactions.** We send a fixed number per window
(`--tx-per-window`, default 12) with a small delay, and we repeat the whole
window `--repetitions` times (default 3) so within-window interference can be
seen as variance rather than mistaken for a rule.

**Competition inside the block.** Other users' transactions interleave with
ours. We therefore key the analysis on **our own transactions' relative order**,
never on absolute block position, and we report how many of the block's
transactions were ours (`total_txs` vs `len(our_txs)`).

**Send order vs arrival order.** What we control is send order; what the leader
sees is arrival order. We label the hypothesis `SEND_ORDER` and say explicitly
in the results that it is a proxy for arrival order, not arrival order itself.

**Noise vs rule.** With 3 repetitions × 12 transactions we get 36 observations
per validator. The verdict threshold (80 % of blocks) plus the reported
consistency fractions are the noise control. Where fewer than 5 blocks are
usable the verdict is `INSUFFICIENT` rather than a guess.

**Fee spend.** Capped twice — at build time and again in
`Rpc.send_transaction` — at `SOLANA_ORDER_SPEND_CAP` lamports, default
40,000,000 (0.04 SOL). A plain transfer costs 5,000 lamports, so 288
transactions cost ~1.44 M lamports (0.00144 SOL), roughly 35× under the cap.
Priority fees are explicit and separately capped; we never rely on a client
default.

**Jito bundles and tips.** Never used. The bounty excludes them, and the sender
produces plain transactions only.

**Alpenglow.** The upgrade landed 2026-10-21. Each run records its epoch and
slot; the CLI refuses to mix measurements across the boundary and the README
states which side a given result is on. Run everything on one side.

**Client identity.** `getClusterNodes` reports `clientId` as a string
(`Agave`, `JitoLabs`, `AgaveBam`, `HarmonicAgave`, `Firedancer`,
`HarmonicFiredancer`, `Frankendancer`, `Raiku`, `Rakurai`, `FireBAM`, …). We use
it verbatim and keep the raw `version` string beside it so the grouping can be
re-derived. Stake is matched through `nodePubkey` from `getVoteAccounts`.

---

## Validator selection

`--validators 8 --min-clients 3` by default. Selection round-robins across
client families, largest stake first inside a family, so we get spread rather
than one dominant client. Observed on mainnet at the time of writing:

```
selected   : 8 across 8 clients
             Agave                1
             JitoLabs             1
             AgaveBam             1
             HarmonicAgave        1
             Firedancer           1
             HarmonicFiredancer    1
             Rakurai              1
             FireBAM              1
total_stake : 71,798,427,429,976,082 lamports (~71.8M SOL)
```

That is 8 distinct clients against a required minimum of 3.

---

## Output

`data/results/run_<slot>.json` contains:

- `meta` — RPC URL, mode, slot at start, selection summary, spend accounting,
  RPC call count;
- `schedule` — every leader window we acted on, with validator, client family,
  first/last slot and slot count;
- `runs` — one record per transaction: validator, slot, repetition, index,
  fee, memo, signature;
- `verdicts` — per-validator verdict, confidence, consistency fractions, rank
  correlation, and notes;
- `summary` — the same grouped by client family, plus an overall line.

Raw signatures and slot numbers are included precisely so a judge can verify
every claim with `getBlock` / `getSignatureStatuses` against mainnet.

---

## Keys

Keys never enter the repository. `SOLANA_KEYPAIR_PATH` points at a file
supplied by volume or environment; the CLI refuses to start a live run without
it. The `live` Docker service mounts the key directory read-only and is behind
a Compose profile, so a plain `docker compose up` cannot spend anything.

---

## Known limitations

Stated plainly, because the bounty rewards honesty about noise:

1. **Arrival order is the RPC's.** We do not peer directly with validators, so
   `SEND_ORDER` is a proxy for arrival order.
2. **Sample size.** 8 validators × 3 repetitions is enough to call a rule that
   holds strongly, and not enough to detect a weak one. A `NO_RULE` verdict
   means "no rule detected at this sample size", not "no rule exists".
3. **Public RPC rate limits.** We throttle to 4 calls/s. A burst would be
   faster and would also be rude, and could distort arrival order.
4. **Fee spread.** All our transactions pay the same base fee unless priority
   fees are varied, so the fee-descending hypothesis is only meaningfully
   tested when fees differ. The current design varies them via
   `--priority-fee` in the live path; a run with uniform fees cannot
   distinguish fee-ordering from any other rule.
5. **Single epoch.** All measurements come from one epoch on one side of
   Alpenglow.

---

## Layout

```
src/config.py      experiment parameters, spend caps, env-driven settings
src/rpc.py         JSON-RPC client, rate limiting, spend accounting
src/validators.py  discovery, client classification, stake, selection
src/leader.py      leader-schedule windows
src/sender.py      transaction construction, double spend-cap guard
src/analyzer.py    block parsing, hypothesis tests, verdicts
src/cli.py         entry point (dry-run by default)
tests/             unit tests for the guard and the analysis
```
