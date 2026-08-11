"""Window lock for SURFER-DES-002 §3.3.

The document says the validation window is not opened until candidate selection
is locked. A document cannot enforce that. This module can.

    selection_slice(ds)   -> always available
    validation_slice(ds)  -> raises unless a lock record exists

The lock record is a FILE in the repository, not a flag in memory, so unlocking
is a commit: it carries a timestamp, the candidate chosen, and the reason, and it
cannot be done and then quietly undone. If the record is missing, every path to
post-2019 performance data raises.

Why bother, given that the person holding the lock is the same person who wrote
the rule: the failure mode is not dishonesty, it is a single idle afternoon.
Looking once is enough to contaminate the window, and after that eight years of
purchased data answers a weaker question than it was bought for. A raised
exception is cheaper than remembering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from schema import BAR_COLUMNS, Dataset

# SURFER-DES-002 §3.3. Selection ends with 2019; validation is everything after.
SELECTION_END = date(2019, 12, 31)
LOCK_FILE = Path(__file__).with_name("VALIDATION_UNLOCKED.json")


class WindowLocked(RuntimeError):
    pass


@dataclass(frozen=True)
class Unlock:
    unlocked_at: str
    chosen_candidate: str
    note: str

    @classmethod
    def load(cls) -> "Unlock | None":
        if not LOCK_FILE.exists():
            return None
        d = json.loads(LOCK_FILE.read_text())
        return cls(**d)


def unlock_validation(chosen_candidate: str, note: str) -> Unlock:
    """Record the selection, permanently, before the validation window opens.

    `chosen_candidate` must name one candidate. Unlocking without having chosen
    is the thing the lock exists to prevent, so a vague value is rejected.
    """
    valid = {"A", "B", "C", "D"}
    if chosen_candidate not in valid:
        raise ValueError(
            f"chosen_candidate must be one of {sorted(valid)}; got "
            f"{chosen_candidate!r}. The point of the lock is that the choice is "
            "fixed before the window opens."
        )
    if not note.strip():
        raise ValueError("note is required: record why this candidate was chosen")

    existing = Unlock.load()
    if existing is not None:
        raise WindowLocked(
            f"validation window was already unlocked at {existing.unlocked_at} "
            f"for candidate {existing.chosen_candidate}. It cannot be unlocked "
            "twice - a second selection would be selection on the validation "
            "sample."
        )

    rec = Unlock(
        unlocked_at=datetime.now().isoformat(timespec="seconds"),
        chosen_candidate=chosen_candidate,
        note=note.strip(),
    )
    LOCK_FILE.write_text(json.dumps(rec.__dict__, ensure_ascii=False, indent=2))
    return rec


def _slice(ds: Dataset, keep) -> Dataset:
    bars = ds.bars[ds.bars["session_date"].map(keep)].reset_index(drop=True)
    return Dataset(
        bars=bars[BAR_COLUMNS],
        symbol=ds.symbol,
        source=ds.source,
        interval_minutes=ds.interval_minutes,
        adjustment=ds.adjustment,
        quarantined=ds.quarantined,
        quarantine_reason=ds.quarantine_reason,
    )


def selection_slice(ds: Dataset) -> Dataset:
    """Sessions up to and including SELECTION_END. Always available."""
    return _slice(ds, lambda d: d <= SELECTION_END)


def validation_slice(ds: Dataset) -> Dataset:
    """Sessions after SELECTION_END. Requires an unlock record."""
    rec = Unlock.load()
    if rec is None:
        raise WindowLocked(
            "validation window is locked (SURFER-DES-002 §3.3).\n"
            f"It covers sessions after {SELECTION_END}. Opening it before "
            "candidate selection is locked destroys the only out-of-sample "
            "evidence available.\n"
            "To open it: unlock_validation('A'|'B'|'C'|'D', note='...') and "
            "commit the resulting VALIDATION_UNLOCKED.json."
        )
    return _slice(ds, lambda d: d > SELECTION_END)


def describe_windows(ds: Dataset) -> str:
    sel = selection_slice(ds)
    rec = Unlock.load()
    n_val = sum(1 for d in ds.sessions if d > SELECTION_END)
    lines = [
        f"  selection  (<= {SELECTION_END}) : {len(sel.sessions):5} sessions",
        f"  validation (>  {SELECTION_END}) : {n_val:5} sessions",
    ]
    if rec is None:
        lines.append("  validation status            : LOCKED")
    else:
        lines.append(
            f"  validation status            : unlocked {rec.unlocked_at} "
            f"for candidate {rec.chosen_candidate}"
        )
    if len(sel.sessions) == 0:
        lines.append(
            "  WARNING: no sessions in the selection window. The current data "
            "source (yfinance, 730 days) lies entirely inside the validation "
            "window - selection cannot be run on it at all."
        )
    return "\n".join(lines)
