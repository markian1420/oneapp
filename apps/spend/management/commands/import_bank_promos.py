"""Import published credit card promos from the banks' own promo listings."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.spend.banks import BY_KEY, ISSUERS, SOURCES, fetch
from apps.spend.models import Promo


class Command(BaseCommand):
    help = (
        "Import credit card promos the banks publish on their own sites. "
        "Reads only public promo listings; stores no card of yours."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--bank",
            default="all",
            help="Which issuer to read, or 'all' for every readable one. "
                 "Choices: all, " + ", ".join(sorted(SOURCES)),
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="Show every Philippine issuer and what it publishes, then stop.",
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
        if options["list"]:
            self._list_issuers()
            return

        bank = options["bank"]
        if bank == "all":
            issuers = [BY_KEY[key] for key in SOURCES]
        elif bank in SOURCES:
            issuers = [BY_KEY[bank]]
        elif bank in BY_KEY:
            # Naming a real bank that cannot be read deserves its reason, not
            # a list of valid choices.
            raise CommandError(f"{BY_KEY[bank].name}: {BY_KEY[bank].blocked}")
        else:
            raise CommandError(
                f"Unknown bank {bank!r}. Available: all, "
                + ", ".join(sorted(SOURCES))
            )

        totals = {"created": 0, "updated": 0, "expired": 0, "undated": 0}
        failed = []

        for issuer in issuers:
            self.stdout.write(f"\n--- {issuer.name} ---")
            try:
                promos = fetch(issuer.key)
            except Exception as exc:
                # One bank being down or having redesigned overnight should not
                # cost the other five. The old rows keep their own dates.
                failed.append(issuer.name)
                self.stdout.write(self.style.ERROR(f"  could not read: {exc}"))
                continue

            self.stdout.write(f"  {len(promos)} published")
            result = self._store(issuer.name, promos, options)
            for key in totals:
                totals[key] += result[key]

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{totals['created']} new, {totals['updated']} updated"
            + (f", across {len(issuers)} issuers" if len(issuers) > 1 else "")
        ))
        if totals["expired"]:
            self.stdout.write(f"{totals['expired']} already expired, left out")
        if totals["undated"]:
            self.stdout.write(self.style.WARNING(
                f"{totals['undated']} stated no end date and were dropped"
            ))
        if failed:
            self.stdout.write(self.style.ERROR(
                "Could not read: " + ", ".join(failed)
            ))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run - nothing was written."))

    # ------------------------------------------------------------------

    def _store(self, issuer_name: str, promos, options) -> dict:
        today = timezone.localdate()
        result = {"created": 0, "updated": 0, "expired": 0, "undated": 0}

        with transaction.atomic():
            for promo in promos:
                if not promo.ends_on:
                    # A promo with no expiry is a parse failure or an
                    # announcement, not an eternal offer. Either way it is
                    # dropped rather than shown as live for ever.
                    result["undated"] += 1
                    continue
                if promo.ends_on < today and not options["include_expired"]:
                    result["expired"] += 1
                    continue

                if options["dry_run"]:
                    exists = Promo.objects.filter(
                        issuer=issuer_name, source_ref=promo.reference
                    ).exists()
                    result["updated" if exists else "created"] += 1
                    continue

                _, created = Promo.objects.update_or_create(
                    issuer=issuer_name,
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
                        "source_note": f"{issuer_name} promos page",
                    },
                )
                result["created" if created else "updated"] += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(
            f"  {result['created']} new, {result['updated']} updated"
            + (f", {result['expired']} expired" if result["expired"] else "")
            + (f", {result['undated']} undated" if result["undated"] else "")
        )
        return result

    def _list_issuers(self):
        readable = [i for i in ISSUERS if i.readable]
        rest = [i for i in ISSUERS if not i.readable]

        self.stdout.write(self.style.SUCCESS(
            f"\n{len(readable)} of {len(ISSUERS)} Philippine issuers publish "
            f"enough to import.\n"
        ))
        for issuer in readable:
            self.stdout.write(f"  {issuer.key:14} {issuer.name:22} {issuer.url}")

        self.stdout.write(self.style.WARNING("\nThe rest, and why:\n"))
        for issuer in rest:
            self.stdout.write(f"  {issuer.key:14} {issuer.name}")
            self.stdout.write(f"  {'':14} {issuer.blocked}")
