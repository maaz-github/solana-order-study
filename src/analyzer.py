"""Block parsing and ordering analysis.

This is where the actual measurement happens: given the slots we sent into and
the transactions we sent, work out what rule the leader used to order them.

The analysis is deliberately conservative. We report:

- the observed order of our own transactions inside each block,
- candidate orderings (fee-descending, arrival, none),
- a per-validator verdict with an explicit confidence and a noise estimate,
- and an honest "NO RULE DETECTED" when the data does not support one.

A null result is a valid result here. The bounty says so explicitly.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class ObservedTx:
    signature: str
    slot: int
    index_in_block: int
    fee_lamports: int
    memo: str
    send_order: int
    confirm_time_s: float


@dataclass
class BlockObservation:
    slot: int
    validator: str
    client_family: str
    total_txs: int
    our_txs: list[ObservedTx] = field(default_factory=list)


@dataclass
class OrderingVerdict:
    validator: str
    client_family: str
    blocks_observed: int
    txs_observed: int
    #: fraction of blocks consistent with fee-descending order
    fee_desc_consistency: float
    #: fraction of blocks consistent with our send order
    send_order_consistency: float
    #: fraction of blocks consistent with reverse send order
    reverse_consistency: float
    #: Spearman-ish rank correlation between fee and block index
    fee_rank_correlation: float
    verdict: str          # "FEE_DESC" | "SEND_ORDER" | "REVERSE" | "NO_RULE" | "INSUFFICIENT"
    confidence: float
    notes: list[str] = field(default_factory=list)


def _rank(values: list[float]) -> list[float]:
    """Average ranks, ties shared."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 3:
        return 0.0
    ra, rb = _rank(a), _rank(b)
    n = len(a)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def _is_descending(values: list[float]) -> bool:
    return all(values[i] >= values[i + 1] for i in range(len(values) - 1))


def _is_ascending(values: list[float]) -> bool:
    return all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def analyse_block(obs: BlockObservation) -> dict:
    """Analyse one block: which candidate orderings does it support?"""
    txs = sorted(obs.our_txs, key=lambda t: t.index_in_block)
    if len(txs) < 2:
        return {"slot": obs.slot, "n": len(txs), "fee_desc": None,
                "send_order": None, "reverse": None, "correlation": None}

    fees = [t.fee_lamports for t in txs]
    sends = [t.send_order for t in txs]
    return {
        "slot": obs.slot,
        "n": len(txs),
        "fee_desc": _is_descending(fees),
        "send_order": _is_ascending(sends),
        "reverse": _is_descending(sends),
        "correlation": _spearman(fees, list(range(len(txs)))),
        "fees": fees,
        "send_orders": sends,
        "block_indices": [t.index_in_block for t in txs],
    }


def verdict_for(validator: str, family: str,
                observations: list[BlockObservation]) -> OrderingVerdict:
    per_block = [analyse_block(o) for o in observations]
    usable = [b for b in per_block if b["n"] >= 2]
    n_tx = sum(b["n"] for b in usable)

    if len(usable) < 2 or n_tx < 6:
        return OrderingVerdict(
            validator=validator, client_family=family,
            blocks_observed=len(observations), txs_observed=n_tx,
            fee_desc_consistency=0.0, send_order_consistency=0.0,
            reverse_consistency=0.0, fee_rank_correlation=0.0,
            verdict="INSUFFICIENT", confidence=0.0,
            notes=[f"only {len(usable)} usable blocks / {n_tx} txs"],
        )

    fee_desc = sum(1 for b in usable if b["fee_desc"]) / len(usable)
    send_ord = sum(1 for b in usable if b["send_order"]) / len(usable)
    rev = sum(1 for b in usable if b["reverse"]) / len(usable)
    corr = statistics.fmean(b["correlation"] for b in usable if b["correlation"] is not None)

    # A rule must hold in a clear majority of blocks to be called a rule.
    if fee_desc >= 0.8 and corr < -0.5:
        v, conf = "FEE_DESC", fee_desc
    elif send_ord >= 0.8:
        v, conf = "SEND_ORDER", send_ord
    elif rev >= 0.8:
        v, conf = "REVERSE", rev
    else:
        v, conf = "NO_RULE", max(fee_desc, send_ord, rev)

    notes = []
    if fee_desc > 0.5 and fee_desc < 0.8:
        notes.append(f"fee-descending holds in {fee_desc:.0%} of blocks — suggestive, not conclusive")
    if corr > -0.2 and v == "NO_RULE":
        notes.append(f"fee/index correlation {corr:+.2f} is weak — ordering looks like noise")
    if len(usable) < 5:
        notes.append(f"only {len(usable)} blocks — widen the sample before trusting this")

    return OrderingVerdict(
        validator=validator, client_family=family,
        blocks_observed=len(observations), txs_observed=n_tx,
        fee_desc_consistency=fee_desc, send_order_consistency=send_ord,
        reverse_consistency=rev, fee_rank_correlation=corr,
        verdict=v, confidence=conf, notes=notes,
    )


def summarise(verdicts: list[OrderingVerdict]) -> dict:
    by_family: dict[str, list[OrderingVerdict]] = {}
    for v in verdicts:
        by_family.setdefault(v.client_family, []).append(v)
    return {
        "validators": len(verdicts),
        "families": {
            fam: {
                "n": len(vs),
                "verdicts": [v.verdict for v in vs],
                "mean_confidence": round(statistics.fmean(v.confidence for v in vs), 3),
            }
            for fam, vs in sorted(by_family.items())
        },
        "overall": _overall(verdicts),
    }


def _overall(verdicts: list[OrderingVerdict]) -> str:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    if not counts:
        return "NO DATA"
    top = max(counts, key=lambda k: counts[k])
    if counts[top] / len(verdicts) >= 0.6:
        return f"{top} in {counts[top]}/{len(verdicts)} validators"
    return "MIXED — no single rule dominates"
