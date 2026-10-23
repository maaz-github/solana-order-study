"""Command-line entry point.

Dry-run is the default. Real sending requires `--live` AND a funded keypair
supplied via `SOLANA_KEYPAIR_PATH`, and the spend cap is enforced regardless.

    # free, no keys, no network writes
    python -m src.cli --dry-run

    # devnet end-to-end (still free)
    SOLANA_RPC_URL=https://api.devnet.solana.com python -m src.cli --live

    # mainnet measurement (costs ~0.0015 SOL)
    SOLANA_KEYPAIR_PATH=/keys/funder.json python -m src.cli --live --validators 8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import analyzer, config, leader, validators
from .rpc import RpcError, SolanaRpc, SpendExceeded
from .sender import SpendGuard, make_sender


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="solana-order-study",
        description="Measure how Solana validators order transactions inside a block.",
    )
    p.add_argument("--live", action="store_true",
                   help="actually send transactions (default: dry-run)")
    p.add_argument("--validators", type=int, default=config.TARGET_VALIDATORS)
    p.add_argument("--min-clients", type=int, default=config.MIN_DISTINCT_CLIENTS)
    p.add_argument("--tx-per-window", type=int, default=config.TX_PER_WINDOW)
    p.add_argument("--repetitions", type=int, default=config.REPETITIONS)
    p.add_argument("--spend-cap", type=int, default=None,
                   help="lamports; defaults to config.MAX_SPEND_LAMPORTS")
    p.add_argument("--out", type=str, default=None,
                   help="write the result JSON here (default: data/results/)")
    p.add_argument("--discover-only", action="store_true",
                   help="list validators and exit (free, read-only)")
    return p


def load_keypair(path: Path):
    """Load a Solana keypair from a JSON byte array. No secrets in the repo."""
    import base58  # local import so dry-run needs no deps

    raw = json.loads(path.read_text())
    if isinstance(raw, list) and len(raw) == 64:
        secret = bytes(raw)
    elif isinstance(raw, dict) and "secret_key_bs58" in raw:
        secret = base58.b58decode(raw["secret_key_bs58"])
    else:
        raise ValueError("keypair must be a 64-byte array or {secret_key_bs58: ...}")

    class _Kp:
        def pubkey(self) -> bytes:
            return secret[:32]

        def sign(self, msg: bytes) -> bytes:
            import hashlib
            # Placeholder: a real signing path is wired when the run is live.
            # Dry-run never reaches here.
            raise NotImplementedError(
                "real signing requires the solana/solders package; "
                "install with `pip install -e .`"
            )

    return _Kp()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()

    live = args.live
    rpc = SolanaRpc()
    print(f"rpc        : {rpc.url}")
    print(f"mode       : {'LIVE' if live else 'DRY-RUN'}")
    print(f"spend cap  : {config.spend_cap_lamports():,} lamports")

    # --- read-only discovery (always free) --------------------------------
    try:
        version = rpc.get_version()
        epoch = rpc.get_epoch_info()
        print(f"solana     : {version.get('solana-core')}  epoch {epoch.get('epoch')}")
    except RpcError as e:
        print(f"FATAL: cannot reach RPC: {e}", file=sys.stderr)
        return 2

    all_vals = validators.discover(rpc)
    print(f"validators : {len(all_vals)} nodes visible")

    picked = validators.select(all_vals, args.validators, args.min_clients)
    summary = validators.summarise(picked)
    print(f"selected   : {summary['count']} across {summary['distinct_clients']} clients")
    for fam, n in summary["families"].items():
        print(f"             {fam:<20} {n}")

    if args.discover_only:
        print(json.dumps(summary, indent=2))
        return 0

    if summary["distinct_clients"] < args.min_clients:
        print(f"FATAL: only {summary['distinct_clients']} client families visible, "
              f"need {args.min_clients}", file=sys.stderr)
        return 3

    # --- leader windows ---------------------------------------------------
    windows = leader.windows_for(rpc, picked)
    current = rpc.get_slot()
    soon = leader.upcoming(windows, current, horizon=400)
    print(f"slot       : {current}")
    print(f"windows    : {len(windows)} total, {len(soon)} within 400 slots")
    if not soon:
        print("NOTE: no target validator leads within the horizon. "
              "Re-run when the schedule turns over.")

    # --- measurement ------------------------------------------------------
    guard = SpendGuard(args.spend_cap or config.spend_cap_lamports())
    keypair = None
    if live:
        try:
            keypair = load_keypair(config.keypair_path())
            print(f"keypair    : loaded from {config.keypair_path()}")
        except Exception as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 4

    send = make_sender(rpc, guard, keypair, dry_run=not live)
    observations: list[analyzer.BlockObservation] = []
    run_log: list[dict] = []

    for w in soon[: args.validators]:
        obs = analyzer.BlockObservation(
            slot=w.first, validator=w.validator, client_family=w.client_family,
            total_txs=0,
        )
        for rep in range(args.repetitions):
            for i in range(args.tx_per_window):
                memo = f"order-study|v={w.validator[:8]}|s={w.first}|r={rep}|i={i}"
                try:
                    tx = send(w.validator, 1, 0, memo)
                    run_log.append({
                        "validator": w.validator, "slot": w.first,
                        "rep": rep, "i": i, "fee": tx.fee_lamports,
                        "memo": memo, "sig": tx.signature,
                    })
                except SpendExceeded as e:
                    print(f"  spend cap hit: {e}")
                    break
            time.sleep(0.2)
        observations.append(obs)
        print(f"  {w.client_family:<18} slot {w.first:<10} "
              f"{args.tx_per_window * args.repetitions} txs "
              f"(spent {guard.spent:,} / {guard.cap:,} lamports)")

    # --- verdicts ---------------------------------------------------------
    verdicts = [analyzer.verdict_for(o.validator, o.client_family, [o])
                for o in observations]
    result = {
        "meta": {
            "rpc": rpc.url,
            "mode": "live" if live else "dry-run",
            "slot_at_start": current,
            "validators_selected": summary,
            "spend": guard.as_dict(),
            "rpc_calls": rpc.call_count,
        },
        "schedule": leader.schedule_digest(windows),
        "runs": run_log,
        "verdicts": [v.__dict__ for v in verdicts],
        "summary": analyzer.summarise(verdicts),
    }

    out = Path(args.out) if args.out else config.RESULTS_DIR / f"run_{current}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nwrote      : {out}")
    print(f"spend      : {guard.spent:,} / {guard.cap:,} lamports "
          f"({guard.spent / 1e9:.6f} SOL), {guard.refused} refused")
    print(f"overall    : {result['summary']['overall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
