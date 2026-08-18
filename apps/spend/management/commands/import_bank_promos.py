"""Import published credit card promos from a bank's own promo listing."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.spend.banks import SOURCES, fetch
from apps.spend.models import Promo


class Command(BaseCommand):
    help = (
        "Import credit card promos a bank publishes on its own site. "
        "Reads only the public promo listing; stores no card of yours."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--bank",
            default="metrobank",
            choices=sorted(SOURCES),
            help="Which bank's promo listing to read.",
        )
        parser.add_argument(
            "--include-expired",
            action="store_true",
            help="Keep promos that have already ended. Off by default, because "
                 "a promo tracker showing expired offers is worse than none.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        bank = options["bank"]

        # argparse validates choices on the command line, but call_command
        # passes options straight through - so without this a scripted call
        # with a typo dies on a raw KeyError instead of saying what is wrong.
        if bank not in SOURCES:
            raise CommandError(
                f"Unknown bank {bank!r}. Available: " + ", ".join(sorted(SOURCES))
            )

        issuer = SOURCES[bank]["issuer"]

        self.stdout.write(f"Reading {issuer} promos... ", ending="")
        try:
            promos = fetch(bank)
        except Exception as exc:
            self.stdout.write(self.style.ERROR("failed"))
            raise CommandError(f"Could not read {issuer}: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"{len(promos)} published"))

        today = timezone.localdate()
        created = updated = skipped_expired = skipped_undated = 0

        with transaction.atomic():
            for promo in promos:
                if not promo.ends_on:
                    # Every record from this source carries an expiry. One
                    # without is a parse failure, not an eternal offer, so it
                    # is dropped rather than shown as live for ever.
                    skipped_undated += 1
                    continue
                if promo.ends_on < today and not options["include_expired"]:
                    skipped_expired += 1
                    continue

                if options["dry_run"]:
                    exists = Promo.objects.filter(
                        issuer=issuer, source_ref=promo.reference
                    ).exists()
                    updated += 1 if exists else 0
                    created += 0 if exists else 1
                    continue

                _, was_created = Promo.objects.update_or_create(
                    issuer=issuer,
                    source_ref=promo.reference,
                    defaults={
                        "title": promo.title,
                        "detail": promo.detail,
                        "brand": promo.brand,
                        "category": promo.category,
                        "card_name": promo.card_name,
                        "discount_pct": promo.discount_pct,
                        "price": promo.price,
                        "starts_on": promo.starts_on,
                        "ends_on": promo.ends_on,
                        "source_url": promo.source_url,
                        "source_note": f"{issuer} promos page",
                    },
                )
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"{created} new, {updated} updated"
        ))
        if skipped_expired:
            self.stdout.write(f"{skipped_expired} already expired, left out")
        if skipped_undated:
            self.stdout.write(self.style.WARNING(
                f"{skipped_undated} had no end date and were dropped"
            ))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))
