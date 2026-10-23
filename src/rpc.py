"""Thin JSON-RPC client for Solana with rate limiting and spend accounting.

Deliberately dependency-light: `urllib` + `json` only, so the container stays
small and the measurement instrument has no hidden network behaviour of its own.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from . import config


class SpendExceeded(RuntimeError):
    """Raised when a send would breach the configured spend cap."""


class RpcError(RuntimeError):
    """Raised on a non-zero JSON-RPC error response."""


class SolanaRpc:
    def __init__(self, url: str | None = None, rate_limit: float | None = None):
        self.url = url or config.rpc_url()
        self.rate_limit = rate_limit if rate_limit is not None else config.RPC_RATE_LIMIT
        self._last_call = 0.0
        self._id = 0
        self.spent_lamports = 0
        self.spend_cap = config.spend_cap_lamports()
        self.call_count = 0

    # -- transport ---------------------------------------------------------

    def _throttle(self) -> None:
        if self.rate_limit <= 0:
            return
        gap = 1.0 / self.rate_limit
        wait = self._last_call + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def call(self, method: str, *params: Any) -> Any:
        self._throttle()
        self._id += 1
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": list(params),
        }).encode()
        req = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise RpcError(f"{method}: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RpcError(f"{method}: {e.reason}") from e

        self.call_count += 1
        if "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body.get("result")

    # -- reads -------------------------------------------------------------

    def get_slot(self) -> int:
        return int(self.call("getSlot"))

    def get_block(self, slot: int, encoding: str = "json") -> dict:
        return self.call(
            "getBlock", slot,
            {
                "encoding": encoding,
                "transactionDetails": "full",
                "rewards": False,
                "maxSupportedTransactionVersion": 0,
            },
        )

    def get_leader_schedule(self) -> dict:
        return self.call("getLeaderSchedule") or {}

    def get_epoch_info(self) -> dict:
        return self.call("getEpochInfo")

    def get_version(self) -> dict:
        return self.call("getVersion")

    def get_cluster_nodes(self) -> list[dict]:
        return self.call("getClusterNodes") or []

    def get_vote_accounts(self) -> dict:
        return self.call("getVoteAccounts") or {}

    def get_balance(self, pubkey: str) -> int:
        return int(self.call("getBalance", pubkey).get("value", 0))

    def get_signature_statuses(self, signatures: list[str]) -> list[dict | None]:
        res = self.call("getSignatureStatuses", signatures, {"searchTransactionHistory": True})
        return (res or {}).get("value", [])

    def send_transaction(self, signed_tx_b64: str, expected_fee: int) -> str:
        """Send a signed transaction, enforcing the spend cap first."""
        if self.spent_lamports + expected_fee > self.spend_cap:
            raise SpendExceeded(
                f"refusing to send: {self.spent_lamports + expected_fee} lamports "
                f"would exceed the {self.spend_cap} lamport cap"
            )
        sig = self.call("sendTransaction", signed_tx_b64, {
            "encoding": "base64",
            "skipPreflight": False,
            "preflightCommitment": "confirmed",
        })
        self.spent_lamports += expected_fee
        return sig

    # -- introspection -----------------------------------------------------

    def spend_remaining(self) -> int:
        return max(0, self.spend_cap - self.spent_lamports)
