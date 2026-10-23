"""Tests for the spend guard and the ordering analysis.

These run with no network and no keys, so `docker compose run test` is free.
The analysis tests pin the behaviour that matters: a rule is only called a rule
when it holds in a clear majority of blocks, and thin data yields INSUFFICIENT
rather than a guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analyzer import (  # noqa: E402
    BlockObservation,
    ObservedTx,
    analyse_block,
    summarise,
    verdict_for,
)
from src.sender import SpendGuard  # noqa: E402


def _tx(i: int, fee: int, send_order: int, block_index: int) -> ObservedTx:
    return ObservedTx(
        signature=f"sig{i}", slot=100, index_in_block=block_index,
        fee_lamports=fee, memo=f"m{i}", send_order=send_order, confirm_time_s=0.1,
    )


# ---------------------------------------------------------------------------
# SpendGuard
# ---------------------------------------------------------------------------


def test_guard_allows_until_cap():
    g = SpendGuard(100_000)
    assert g.check(60_000)
    g.record(60_000)
    assert g.check(40_000)
    g.record(40_000)
    assert not g.check(1)
    assert g.remaining == 0


def test_guard_counts_refusals():
    g = SpendGuard(10_000)
    g.refuse()
    g.refuse()
    assert g.refused == 2
    assert g.as_dict()["refused_count"] == 2


def test_guard_never_negative():
    g = SpendGuard(0)
    assert not g.check(1)
    assert g.remaining == 0


# ---------------------------------------------------------------------------
# Block analysis
# ---------------------------------------------------------------------------


def test_fee_descending_detected():
    obs = BlockObservation(slot=1, validator="v", client_family="Agave", total_txs=3)
    # block order: highest fee first
    obs.our_txs = [_tx(0, 9000, 0, 0), _tx(1, 7000, 1, 1), _tx(2, 5000, 2, 2)]
    b = analyse_block(obs)
    assert b["fee_desc"] is True
    assert b["send_order"] is True


def test_no_rule_when_mixed():
    obs = BlockObservation(slot=1, validator="v", client_family="Agave", total_txs=4)
    # fees in block order: 5000, 9000, 7000, 6000 — neither asc nor desc.
    # Send order in block: 0,1,2,3 — that IS ascending, so only the fee
    # hypothesis is refuted here. The point of the case is that a fee rule
    # does not hold, not that every hypothesis fails.
    obs.our_txs = [_tx(0, 5000, 0, 0), _tx(1, 9000, 1, 1),
                   _tx(2, 7000, 2, 2), _tx(3, 6000, 3, 3)]
    b = analyse_block(obs)
    assert b["fee_desc"] is False
    assert b["send_order"] is True


def test_neither_fee_nor_send_order():
    obs = BlockObservation(slot=1, validator="v", client_family="Agave", total_txs=4)
    # fees 5000,9000,7000,6000 (not monotonic) and send order 2,0,3,1 (not
    # monotonic in either direction) — no candidate rule survives.
    obs.our_txs = [_tx(0, 5000, 2, 0), _tx(1, 9000, 0, 1),
                   _tx(2, 7000, 3, 2), _tx(3, 6000, 1, 3)]
    b = analyse_block(obs)
    assert b["fee_desc"] is False
    assert b["send_order"] is False
    assert b["reverse"] is False


def test_single_tx_block_is_uninformative():
    obs = BlockObservation(slot=1, validator="v", client_family="Agave", total_txs=1)
    obs.our_txs = [_tx(0, 5000, 0, 0)]
    b = analyse_block(obs)
    assert b["n"] == 1
    assert b["fee_desc"] is None


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def _blocks(n: int, fee_desc: bool, send_order: bool) -> list[BlockObservation]:
    out = []
    for k in range(n):
        obs = BlockObservation(slot=k, validator="v", client_family="Agave",
                               total_txs=4)
        if fee_desc and send_order:
            fees, sends = [9000, 7000, 6000, 5000], [0, 1, 2, 3]
        elif fee_desc:
            fees, sends = [9000, 7000, 6000, 5000], [3, 1, 0, 2]
        elif send_order:
            fees, sends = [5000, 9000, 7000, 6000], [0, 1, 2, 3]
        else:
            fees, sends = [5000, 9000, 7000, 6000], [2, 0, 3, 1]
        obs.our_txs = [_tx(i, fees[i], sends[i], i) for i in range(4)]
        out.append(obs)
    return out


def test_insufficient_data_is_not_a_guess():
    v = verdict_for("v", "Agave", _blocks(1, True, True))
    assert v.verdict == "INSUFFICIENT"
    assert v.confidence == 0.0


def test_fee_desc_verdict():
    v = verdict_for("v", "Agave", _blocks(6, True, True))
    assert v.verdict == "FEE_DESC"
    assert v.confidence >= 0.8


def test_no_rule_verdict():
    v = verdict_for("v", "Agave", _blocks(6, False, False))
    assert v.verdict == "NO_RULE"


def test_summary_groups_by_family():
    vs = [
        verdict_for("a", "Agave", _blocks(6, True, True)),
        verdict_for("b", "Firedancer", _blocks(6, False, False)),
    ]
    s = summarise(vs)
    assert s["validators"] == 2
    assert set(s["families"]) == {"Agave", "Firedancer"}
    assert "MIXED" in s["overall"] or "FEE_DESC" in s["overall"]
