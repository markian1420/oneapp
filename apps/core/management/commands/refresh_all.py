"""
Refresh every upstream source, in one command.

Run sequentially on purpose. SQLite takes a single writer, and running two
importers at once produced "database is locked" the one time it was tried.

One source failing does not stop the others. The DOE site has been down for
this entire project, Overpass rate-limits hard, and the DA publishes late in
the day - a refresh that aborts on the first 404 would almost never finish.
Every attempt is recorded either way, so a failure is distinguishable from
nobody having run it.
"""

from __future__ import annotations

import io
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.core.freshness import BY_KEY, SOURCES
from apps.core.models import SourceRun

# Places are excluded from a routine refresh: the import is hundreds of
# Overpass queries against public instances that rate-limit hard, and new shops
# get mapped over months, not hours. Ask for it explicitly with --places.
ROUTINE = [s for s in SOURCES if s.key != "places_osm"]


class Command(BaseCommand):
    help = (
        "Refresh every source on its own cadence. Safe to run daily; each "
        "source only does real work when it has something new."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--only", action="append", default=[], metavar="SOURCE",
            help="Refresh just these sources. Repeatable. "
                 "Choices: " + ", ".join(s.key for s in SOURCES),
        )
        parser.add_argument(
            "--places", action="store_true",
            help="Also re-import places from OpenStreetMap. Slow and heavy; "
                 "left out of the routine refresh on purpose.",
        )
        parser.add_argument(
            "--due-only", action="store_true",
            help="Skip anything already current. Useful on a frequent timer.",
        )
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit non-zero if any source failed. For a scheduler that "
                 "only tells you about a run when it comes back unhappy.",
        )

    def handle(self, *args, **options):
        from apps.core.freshness import statuses

        wanted = options["only"]
        if wanted:
            unknown = [key for key in wanted if key not in BY_KEY]
            if unknown:
                self.stderr.write(f"Unknown source(s): {', '.join(unknown)}")
                return
            sources = [BY_KEY[key] for key in wanted]
        else:
            sources = list(ROUTINE)
            if options["places"]:
                sources.append(BY_KEY["places_osm"])

        if options["due_only"]:
            state = {s.source.key: s for s in statuses()}
            sources = [
                s for s in sources
                if state[s.key].state in {"due", "stale", "failed", "never"}
            ]
            if not sources:
                self.stdout.write(self.style.SUCCESS(
                    "Everything is current. Nothing to do."
                ))
                return

        succeeded = failed = 0
        for source in sources:
            self.stdout.write(f"\n=== {source.name} ({source.cadence}) ===")
            started = time.monotonic()

            captured = io.StringIO()
            ok, detail, rows = True, "", 0
            try:
                parts = source.command.split()
                call_command(parts[0], *parts[1:], stdout=captured,
                             stderr=captured)
                detail = self._summarise(captured.getvalue())
                rows = self._rows(captured.getvalue())
            except Exception as exc:
                ok = False
                detail = str(exc)[:250]

            elapsed = time.monotonic() - started
            SourceRun.objects.create(
                source=source.key, ok=ok, rows=rows,
                detail=detail[:250], duration_seconds=round(elapsed, 1),
            )

            if ok:
                succeeded += 1
                self.stdout.write(self.style.SUCCESS(f"  {detail or 'done'}"))
            else:
                failed += 1
                # Reported, not raised: the next source may well be fine.
                self.stdout.write(self.style.ERROR(f"  failed: {detail}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{succeeded} source(s) refreshed"
            + (f", {failed} failed" if failed else "")
        ))
        if failed:
            self.stdout.write(
                "A failure here is usually the source being down or rate "
                "limiting, not the app. The old data is untouched and still "
                "labelled with its own date."
            )
            if options["strict"]:
                raise CommandError(f"{failed} source(s) failed")

    def _summarise(self, output: str) -> str:
        """The last meaningful line an importer printed."""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            if any(word in line.lower() for word in
                   ("written", "imported", "new", "updated", "day(s)", "rows")):
                return line
        return lines[-1] if lines else ""

    def _rows(self, output: str) -> int:
        import re

        match = re.search(r"(\d+)\s+(?:row|grade|day|place)", output)
        return int(match.group(1)) if match else 0
