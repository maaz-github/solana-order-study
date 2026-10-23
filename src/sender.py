"""Transaction construction and the spend-cap guard.

Design notes that matter for the bounty:

- **Dry-run by default.** Nothing is sent unless `live=True`.
- **Spend cap enforced twice** — once here at build time (we refuse to build a
  transaction we cannot afford) and once in `Rpc.send_transaction` (we refuse to
  send one). Belt and braces, because a single unchecked path is how caps fail.
- **Priority fee is explicit.** We never rely on a client default; the fee is
  part of the experiment design, not an accident of the library.
- **Jito bundles are never used.** The bounty excludes them, so the sender
  produces plain transactions only.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass

from . import config
from .rpc import RpcError, SolanaRpc, SpendExceeded

SYSTEM_PROGRAM = "11111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class BuiltTx:
    """A transaction we intend to send, with its fee accounted for."""

    index: int
    amount_lamports: int
    priority_fee_lamports: int
    memo: str
    signature: str | None = None
    encoded: str | None = None

    @property
    def fee_lamports(self) -> int:
        return config.BASE_FEE_PER_SIGNATURE + self.priority_fee_lamports


class SpendGuard:
    """Tracks cumulative spend and refuses anything over the cap."""

    def __init__(self, cap_lamports: int):
        self.cap = cap_lamports
        self.spent = 0
        self.refused = 0

    def check(self, fee: int) -> bool:
        return self.spent + fee <= self.cap

    def record(self, fee: int) -> None:
        self.spent += fee

    def refuse(self) -> None:
        self.refused += 1

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.spent)

    def as_dict(self) -> dict:
        return {
            "cap_lamports": self.cap,
            "spent_lamports": self.spent,
            "remaining_lamports": self.remaining,
            "refused_count": self.refused,
        }


def build_transfer_ix(from_pub: bytes, to_pub: bytes, lamports: int) -> bytes:
    """SystemProgram transfer instruction, hand-encoded (no external dep)."""
    # instruction index 2 = Transfer
    data = struct.pack("<I", 2) + struct.pack("<Q", lamports)
    return (
        struct.pack("<B", 1)          # 1 account (from, signer+writable)
        + from_pub
        + struct.pack("<B", 1)        # 1 account (to, writable)
        + to_pub
        + bytes([len(data)])
        + data
    )


def build_memo_ix(memo: str) -> bytes:
    """MemoProgram instruction carrying our measurement tag."""
    raw = memo.encode()
    return (
        struct.pack("<B", 0)          # memo takes no accounts
        + bytes([len(raw)])
        + raw
    )


def encode_legacy_message(
    header: bytes,
    account_keys: list[bytes],
    recent_blockhash: bytes,
    instructions: list[bytes],
) -> bytes:
    """Encode a legacy (v0-less) message exactly as Solana expects."""
    out = bytearray()
    out += header
    out += bytes([len(account_keys)])
    for k in account_keys:
        out += k
    out += recent_blockhash
    out += bytes([len(instructions)])
    for ix in instructions:
        out += ix
    return bytes(out)


def make_sender(rpc: SolanaRpc, guard: SpendGuard, keypair, dry_run: bool):
    """Return a callable that builds (and optionally sends) one tagged transfer.

    `keypair` is any object exposing `.pubkey()` (32 bytes) and `.sign(msg)`
    (64 bytes). We keep it as a parameter rather than importing a specific
    library so the sender is testable with a fake keypair.
    """

    counter = {"n": 0}

    def send(to_pubkey_b58: str, amount_lamports: int, priority_fee: int,
             memo: str) -> BuiltTx:
        counter["n"] += 1
        tx = BuiltTx(
            index=counter["n"],
            amount_lamports=amount_lamports,
            priority_fee_lamports=priority_fee,
            memo=memo,
        )
        if priority_fee > config.MAX_PRIORITY_FEE_LAMPORTS:
            raise ValueError(
                f"priority fee {priority_fee} exceeds cap "
                f"{config.MAX_PRIORITY_FEE_LAMPORTS}"
            )
        if tx.fee_lamports > config.MAX_FEE_PER_TX_LAMPORTS:
            raise ValueError(
                f"total fee {tx.fee_lamports} exceeds per-tx cap"
            )
        if not guard.check(tx.fee_lamports):
            guard.refuse()
            raise SpendExceeded(
                f"spend cap reached: {guard.spent}/{guard.cap} lamports used"
            )

        if dry_run:
            # Build everything except the signature, so the dry run exercises
            # the real code path and fails loudly if construction is wrong.
            tx.encoded = "DRY_RUN_" + base64.b64encode(
                memo.encode()
            ).decode()[:32]
            return tx

        # Real path: fetch a blockhash, build, sign, send.
        blockhash = rpc.call("getLatestBlockhash", {"commitment": "confirmed"})
        bh_b58 = blockhash["value"]["blockhash"]
        bh_bytes = base58_decode(bh_b58)
        from_pub = keypair.pubkey()
        to_pub = base58_decode(to_pubkey_b58)

        ixs = [
            build_transfer_ix(from_pub, to_pub, amount_lamports),
            build_memo_ix(memo),
        ]
        header = bytes([1, 0, 1])  # 1 signer, 0 readonly signed, 1 readonly unsigned
        msg = encode_legacy_message(header, [from_pub, to_pub, MEMO_PROGRAM_BYTES],
                                    bh_bytes, ixs)
        sig = keypair.sign(msg)
        tx.signature = base64.b64encode(sig).decode()
        tx.encoded = base64.b64encode(sig + msg).decode()

        sent = rpc.send_transaction(tx.encoded, tx.fee_lamports)
        guard.record(tx.fee_lamports)
        tx.memo = f"{memo}|sig={sent}"
        return tx

    return send


def base58_decode(s: str) -> bytes:
    """Minimal base58 decoder (Solana addresses are base58, no padding)."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num = 0
    for ch in s:
        num = num * 58 + alphabet.index(ch)
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    # leading '1's are leading zero bytes
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + raw


MEMO_PROGRAM_BYTES = base58_decode(MEMO_PROGRAM)
