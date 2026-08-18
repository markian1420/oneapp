"""Shell-level models: only what has no better home."""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class SourceRun(models.Model):
    """One attempt to refresh one upstream source.

    Recorded whether it succeeded or not. A failure that leaves no trace looks
    identical to never having tried, and the difference matters: one means the
    source is down, the other means nobody ran it.
    """

    source = models.CharField(max_length=40, db_index=True)
    ran_at = models.DateTimeField(default=timezone.now, db_index=True)
    ok = models.BooleanField(default=True)
    rows = models.PositiveIntegerField(
        default=0, help_text="Rows written or updated by this run."
    )
    detail = models.CharField(max_length=250, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-ran_at"]
        indexes = [
            models.Index(fields=["source", "-ran_at"], name="idx_sourcerun_latest"),
        ]

    def __str__(self) -> str:
        return f"{self.source} {self.ran_at:%Y-%m-%d %H:%M} {'ok' if self.ok else 'failed'}"
