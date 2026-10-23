"""Configuration for the validator transaction-ordering measurement study.

Every tunable lives here so the experiment is reproducible from one file.
Keys are NEVER read from this module — they come from the environment
(see `config.py::load_keypair`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Network endpoints
# ---------------------------------------------------------------------------

MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEVNET_RPC = "https://api.devnet.solana.com"
LOCALNET_RPC = "http://127.0.0.1:8899"

# ---------------------------------------------------------------------------
# Spend control
# ---------------------------------------------------------------------------

#: Hard ceiling on total lamports the study may spend on transaction fees.
#: The bounty asks the whole measurement to fit in ~0.05 SOL. We default to
#: 0.04 SOL so there is headroom for retries without breaching the brief.
MAX_SPEND_LAMPORTS = int(0.04 * 1_000_000_000)

#: Per-transaction fee ceiling. Solana's base fee is 5,000 lamports per
#: signature; a plain transfer has one signature. Priority fees are added on
#: top, so we cap them explicitly rather than trusting the client default.
MAX_PRIORITY_FEE_LAMPORTS = 50_000

#: Absolute per-transaction ceiling (base + priority).
MAX_FEE_PER_TX_LAMPORTS = 100_000

#: Solana base fee per signature (lamports). A plain transfer has one.
BASE_FEE_PER_SIGNATURE = 5_000

# ---------------------------------------------------------------------------
# Experiment shape
# ---------------------------------------------------------------------------

#: Number of validators to measure. Bounty requires >= 6.
TARGET_VALIDATORS = 8

#: Minimum distinct client/fork versions to span. Bounty requires >= 3.
MIN_DISTINCT_CLIENTS = 3

#: Transactions to send per validator leader window.
TX_PER_WINDOW = 12

#: Number of independent repetitions per validator (for noise estimation).
REPETITIONS = 3

#: Seconds to wait for a sent transaction to be confirmed before giving up.
CONFIRM_TIMEOUT_S = 60

#: RPC calls per second. Deliberately low: this is a measurement instrument,
#: not a load test, and we must not degrade the public endpoint.
RPC_RATE_LIMIT = 4

# ---------------------------------------------------------------------------
# Alpenglow
# ---------------------------------------------------------------------------

#: The bounty requires all measurements on one side of the Alpenglow upgrade
#: (2026-10-21). We record which side each run is on and refuse to mix.
ALPENGLOW_BOUNDARY_SLOT: int | None = None  # set from CLI once known

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RESULTS_DIR = DATA_DIR / "results"


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Environment-driven settings
# ---------------------------------------------------------------------------


def rpc_url() -> str:
    return os.environ.get("SOLANA_RPC_URL", MAINNET_RPC)


def keypair_path() -> Path:
    """Path to the funding keypair. Never a literal key in the repo."""
    p = os.environ.get("SOLANA_KEYPAIR_PATH")
    if not p:
        raise RuntimeError(
            "SOLANA_KEYPAIR_PATH is not set. Keys are supplied via env/volume only."
        )
    return Path(p)


def is_live() -> bool:
    """True only when the caller explicitly opts into real sending."""
    return os.environ.get("SOLANA_ORDER_LIVE", "").lower() in ("1", "true", "yes")


def spend_cap_lamports() -> int:
    return int(os.environ.get("SOLANA_ORDER_SPEND_CAP", MAX_SPEND_LAMPORTS))


@dataclass
class RunConfig:
    """Resolved configuration for a single run."""

    rpc: str = field(default_factory=rpc_url)
    live: bool = False
    spend_cap: int = field(default_factory=spend_cap_lamports)
    validators: int = TARGET_VALIDATORS
    min_clients: int = MIN_DISTINCT_CLIENTS
    tx_per_window: int = TX_PER_WINDOW
    repetitions: int = REPETITIONS
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "RunConfig":
        return cls(
            rpc=rpc_url(),
            live=is_live(),
            spend_cap=spend_cap_lamports(),
            dry_run=not is_live(),
        )
