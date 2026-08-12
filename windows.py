"""Window lock for SURFER-DES-002 §3.3 — REPEALED 2026-08-12.

The user repealed the selection/validation split in order to choose a candidate
on the data currently available (yfinance, 730 days, 2024-08 to 2026-08).

CONSEQUENCE, recorded here rather than argued: after this, NO OUT-OF-SAMPLE
EVIDENCE EXISTS. The chosen candidate will be the one that fitted a two-year
window in which the underlying mostly rose, and nothing remains to test whether
that fit generalises. Any later claim that SURFER was validated must be read
against this note.

The machinery is kept, not deleted, so the split can be restored once
selection-window data is purchased — and so this decision stays visible in the
file rather than vanishing from the repository.

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

# Restored 2026-08-12 (SURFER-DES-002 §12.3). Candidate D was selected on
# 2024-08 onward, so everything BEFORE that is untouched by selection and is
# genuine out-of-sample. The repeal turned out to cost nothing: the split came
# back on its own once the choice was made, with the boundary reversed.
SPLIT_REPEALED = False

# §12.4 (corrected 2026-08-12). An earlier value of 2024-08-01 was wrong: the
# candidate comparison that chose D ran on the WHOLE 730-day yfinance feed, not
# on its recent half. Everything the free feed contains is therefore spent, and
# the 222 sessions this boundary had classified as out-of-sample were not.
SELECTION_USED_FROM = date(2024, 8, 1)

# The whole free feed was consumed by selection. Out-of-sample evidence must
# come from a source that reaches further back than 730 days.
FREE_FEED_FULLY_SPENT = True
FREE_FEED_SOURCES = ("yfinance",)

# Second, independent reason the free feed cannot serve as validation: the engine
# needs 284 sessions of history (252-session percentile + 12-bar ATR + 20 warm-up)
# before its gate matches the pre-registered spec. A 222-session slice tops out at
# 202 readings, so the gate would be running a DIFFERENT rule than §4.1 defines -
# and relaxing the spec in order to validate is what pre-registration forbids.
MIN_SESSIONS_FOR_ENGINE = 284
REPEAL_NOTE = (
    "§3.3 폐기 2026-08-12 (사용자 결정). 야후 730일 구간에서 후보를 선택함. "
    "표본 외 검증 없음 — 선택된 후보는 상승 구간 2년에 맞춰진 것이며, "
    "그 적합이 일반화되는지 시험할 데이터가 남아 있지 않음."
)
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
    """Sessions already used for selection: 2024-08-01 onward (§12.4).

    Named "selection" because that is what they were spent on. Re-running a
    comparison here cannot produce new evidence - the candidate was chosen from
    exactly these bars.
    """
    return _slice(ds, lambda d: d >= SELECTION_USED_FROM)


def validation_slice(ds: Dataset) -> Dataset:
    """Sessions BEFORE 2024-08-01 - untouched by selection. Requires an unlock.

    This is the only out-of-sample evidence that exists. It holds 2018Q4, 2020Q1
    and 2022, which is where a volatility gate's cost would appear if it has one.
    Spending it casually leaves nothing.
    """
    if FREE_FEED_FULLY_SPENT and any(
        src in ds.source for src in FREE_FEED_SOURCES
    ):
        raise WindowLocked(
            f"{ds.source} cannot supply out-of-sample evidence.\n"
            "The candidate comparison that selected D ran on this entire feed, so "
            "every session in it is spent. And at 730 days the feed is shorter "
            f"than the {MIN_SESSIONS_FOR_ENGINE} sessions the engine needs before "
            "its gate matches §4.1 — a shorter slice runs a different rule.\n"
            "§2.2R needs purchased history (FirstRate, pre-2023)."
        )
    if SPLIT_REPEALED:
        return _slice(ds, lambda d: d < SELECTION_USED_FROM)
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
    return _slice(ds, lambda d: d < SELECTION_USED_FROM)


def describe_windows(ds: Dataset) -> str:
    sel = selection_slice(ds)
    rec = Unlock.load()
    n_val = sum(1 for d in ds.sessions if d < SELECTION_USED_FROM)
    lines = [
        f"  selection  (>= {SELECTION_USED_FROM}) : {len(sel.sessions):5} sessions  (spent)",
        f"  validation (<  {SELECTION_USED_FROM}) : {n_val:5} sessions  (out-of-sample)",
    ]
    if SPLIT_REPEALED:
        lines.append("  validation status            : SPLIT REPEALED 2026-08-12")
        lines.append("  out-of-sample evidence       : NONE")
    elif rec is None:
        lines.append("  validation status            : LOCKED")
    else:
        lines.append(
            f"  validation status            : unlocked {rec.unlocked_at} "
            f"for candidate {rec.chosen_candidate}"
        )
    spent = FREE_FEED_FULLY_SPENT and any(
        src in ds.source for src in FREE_FEED_SOURCES
    )
    if spent:
        lines = [
            f"  sessions available          : {len(ds.sessions):5}",
            "  out-of-sample               :     0  (whole feed spent on selection)",
            f"  engine needs                : {MIN_SESSIONS_FOR_ENGINE:5} sessions for a §4.1-spec gate",
        ]
    if n_val == 0 and not spent:
        lines.append(
            "  NOTE: no out-of-sample sessions in this feed."
        )
    if len(sel.sessions) == 0 and not SPLIT_REPEALED:
        lines.append(
            "  WARNING: no sessions in the selection window. The current data "
            "source (yfinance, 730 days) lies entirely inside the validation "
            "window - selection cannot be run on it at all."
        )
    return "\n".join(lines)
