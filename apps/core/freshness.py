"""
How current each source actually is.

"Real time" is not available here and mostly would not mean anything. Philippine
pump prices move once a week, on a Tuesday. The DA publishes its commodity index
once a weekday, and late in the day - at two in the afternoon today's edition was
still a 404. Bank promos change when a bank decides to run one. None of this
ticks.

What is achievable is that every number on screen is as fresh as its source
allows, and that the app says how old it is rather than letting a stale figure
look current. So each source declares the cadence it genuinely publishes on, and
anything past that is marked overdue instead of quietly ageing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone


# A note on due_after_hours: it has to be shorter than the gap between runs,
# or a daily source is skipped by every other scheduled pass and refreshes at
# half the rate it publishes. The schedule runs twice a day, so twenty hours
# means the morning pass always finds yesterday's edition waiting.


@dataclass(frozen=True)
class Source:
    """One upstream feed and how often it really publishes."""

    key: str
    name: str
    command: str
    cadence: str
    # Hours after which the data is behind what the source would have.
    due_after_hours: int
    # Hours after which it is old enough to distrust.
    stale_after_hours: int
    note: str = ""


SOURCES: tuple[Source, ...] = (
    Source(
        key="fuel_metrofuel",
        name="Fuel, per-brand averages",
        command="import_metrofuel",
        cadence="Daily",
        due_after_hours=20,
        stale_after_hours=24 * 5,
        note="MetroFuel Tracker recomputes daily from reported prices.",
    ),
    Source(
        key="fuel_gaswatch",
        name="Fuel, per-station survey",
        command="import_gaswatch",
        cadence="Daily",
        due_after_hours=20,
        stale_after_hours=24 * 10,
        note="A price per station for most of Metro Manila, plus a regional "
             "median for the stations it cannot be matched to.",
    ),
    Source(
        key="grocery_da",
        name="Grocery commodity index",
        command="import_da_prices",
        cadence="Weekdays",
        due_after_hours=20,
        stale_after_hours=24 * 5,
        note="The DA publishes on weekdays, often late in the day. Asked for "
             "daily: a weekday edition missed is a gap in the history that "
             "nothing later fills, and a weekend ask costs one skipped fetch.",
    ),
    Source(
        key="promos_banks",
        name="Card promos",
        command="import_bank_promos --bank all",
        cadence="Weekly",
        due_after_hours=24 * 8,
        stale_after_hours=24 * 30,
        note="Six issuers publish readably; the other eleven do not.",
    ),
    Source(
        key="places_osm",
        name="Places on the map",
        command="import_places --area NCR --kind all",
        cadence="Every few months",
        due_after_hours=24 * 90,
        stale_after_hours=24 * 180,
        note="New shops get mapped slowly, and the import is heavy.",
    ),
)

BY_KEY = {source.key: source for source in SOURCES}


@dataclass
class Status:
    """Where one source stands right now."""

    source: Source
    last_run: object = None          # SourceRun or None
    age_hours: float | None = None
    rows: int = 0
    ok: bool = True

    @property
    def state(self) -> str:
        if self.last_run is None:
            return "never"
        if not self.ok:
            return "failed"
        if self.age_hours is None:
            return "never"
        if self.age_hours >= self.source.stale_after_hours:
            return "stale"
        if self.age_hours >= self.source.due_after_hours:
            return "due"
        return "current"

    @property
    def label(self) -> str:
        return {
            "current": "Current",
            "due": "Due a refresh",
            "stale": "Stale",
            "failed": "Last run failed",
            "never": "Never fetched",
        }[self.state]

    @property
    def badge_class(self) -> str:
        return {
            "current": "badge-success",
            "due": "badge-warning",
            "stale": "badge-danger",
            "failed": "badge-danger",
            "never": "badge-muted",
        }[self.state]

    @property
    def age_label(self) -> str:
        if self.age_hours is None:
            return "never"
        if self.age_hours < 1:
            return "just now"
        if self.age_hours < 48:
            return f"{int(self.age_hours)}h ago"
        return f"{int(self.age_hours // 24)}d ago"

    @property
    def needs_attention(self) -> bool:
        return self.state in {"due", "stale", "failed", "never"}


def statuses() -> list[Status]:
    """Every source, with how current it is.

    Measured from recorded runs rather than from the data, because a run that
    fetched nothing new is still evidence the app looked - and a source that
    has genuinely not changed should not be reported as going stale.
    """
    from .models import SourceRun

    now = timezone.now()
    latest: dict[str, SourceRun] = {}
    for run in SourceRun.objects.order_by("-ran_at"):
        latest.setdefault(run.source, run)

    result = []
    for source in SOURCES:
        run = latest.get(source.key)
        status = Status(source=source, last_run=run)
        if run:
            status.age_hours = (now - run.ran_at).total_seconds() / 3600
            status.rows = run.rows
            status.ok = run.ok
        result.append(status)
    return result


def summary() -> dict:
    """Headline counts for the briefing."""
    all_statuses = statuses()
    return {
        "statuses": all_statuses,
        "current": sum(1 for s in all_statuses if s.state == "current"),
        "total": len(all_statuses),
        "attention": [s for s in all_statuses if s.needs_attention],
    }
