"""Leader-schedule detection: when is a target validator the block leader?

We need to know, for each chosen validator, which slots it leads so we can send
transactions inside its window. `getLeaderSchedule` gives the answer directly;
this module wraps it and records the raw data for verification.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rpc import SolanaRpc
from .validators import Validator


@dataclass
class LeaderWindow:
    validator: str
    slots: list[int]
    client_family: str

    @property
    def first(self) -> int:
        return self.slots[0] if self.slots else -1

    @property
    def last(self) -> int:
        return self.slots[-1] if self.slots else -1


def windows_for(rpc: SolanaRpc, validators: list[Validator]) -> list[LeaderWindow]:
    """Return the leader slots for each target validator in the current epoch."""
    schedule = rpc.get_leader_schedule()
    out: list[LeaderWindow] = []
    for v in validators:
        raw = schedule.get(v.identity) or schedule.get(v.vote_account or "")
        if not raw:
            continue
        slots = sorted(int(s) for s in raw)
        if slots:
            out.append(LeaderWindow(
                validator=v.identity,
                slots=slots,
                client_family=v.client_family,
            ))
    return out


def upcoming(
    windows: list[LeaderWindow],
    current_slot: int,
    horizon: int = 200,
) -> list[LeaderWindow]:
    """Windows whose first slot is within `horizon` of the current slot."""
    return [w for w in windows if 0 <= w.first - current_slot <= horizon]


def schedule_digest(windows: list[LeaderWindow]) -> dict:
    """Compact, judge-verifiable summary of the schedule we acted on."""
    return {
        "windows": [
            {
                "validator": w.validator,
                "client_family": w.client_family,
                "first_slot": w.first,
                "last_slot": w.last,
                "slot_count": len(w.slots),
            }
            for w in windows
        ],
        "total_slots": sum(len(w.slots) for w in windows),
    }
