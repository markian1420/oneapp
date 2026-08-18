"""
Places: everywhere you spend money, on one map.

This started life as the fuel module's Station, and every field on it turned
out to be generic - an OSM identity, a name, a brand, coordinates and an
administrative region. A wet market, a Puregold and a Jollibee need exactly
that and nothing more. Keeping them in one table rather than one per module is
what lets a single bounding-box query answer "what is around me", instead of
one query per kind and no way to rank across them.

Module-specific data hangs off a place by foreign key: a fill-up points at a
fuel place, a basket at a supermarket. The place itself stays dumb.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class PlaceKind(models.TextChoices):
    """What a place is, from the app's point of view.

    Deliberately coarser than OSM's tagging. OSM separates a mall from a
    department store; for deciding where to shop that is a distinction without
    a difference, and two pins on the same building is worse than one.
    """

    FUEL = "fuel", "Fuel station"
    MARKET = "market", "Public market"
    SUPERMARKET = "supermarket", "Supermarket"
    CONVENIENCE = "convenience", "Convenience store"
    FAST_FOOD = "fast_food", "Fast food"
    RESTAURANT = "restaurant", "Restaurant"
    MALL = "mall", "Mall or department store"
    PHARMACY = "pharmacy", "Pharmacy"


# How each kind is drawn on the map. Shape carries the meaning, colour only
# reinforces it, so the map stays readable in greyscale and for a colourblind
# reader - the pin for fuel is not merely "the green one".
KIND_STYLE = {
    PlaceKind.FUEL: {"icon": "fuel", "tone": "brand"},
    PlaceKind.MARKET: {"icon": "basket", "tone": "amber"},
    PlaceKind.SUPERMARKET: {"icon": "basket", "tone": "sky"},
    PlaceKind.CONVENIENCE: {"icon": "basket", "tone": "indigo"},
    PlaceKind.FAST_FOOD: {"icon": "receipt", "tone": "red"},
    PlaceKind.RESTAURANT: {"icon": "receipt", "tone": "rose"},
    PlaceKind.MALL: {"icon": "wallet", "tone": "violet"},
    PlaceKind.PHARMACY: {"icon": "shield-check", "tone": "teal"},
}


class Place(models.Model):
    """Somewhere you can spend money, sourced from OpenStreetMap."""

    class OSMType(models.TextChoices):
        NODE = "node", "Node"
        WAY = "way", "Way"
        RELATION = "relation", "Relation"

    kind = models.CharField(max_length=20, choices=PlaceKind.choices, db_index=True)

    osm_type = models.CharField(max_length=8, choices=OSMType.choices)
    osm_id = models.BigIntegerField()

    name = models.CharField(max_length=200, blank=True)
    brand = models.CharField(
        max_length=60,
        blank=True,
        db_index=True,
        help_text="Canonical brand, e.g. Petron or Puregold. Normalised on import.",
    )
    brand_raw = models.CharField(
        max_length=120,
        blank=True,
        help_text="Whatever OSM had, kept so a bad normalisation can be traced.",
    )

    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    street = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=120, blank=True, db_index=True)
    province = models.CharField(max_length=120, blank=True)
    region = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        help_text="DOE/DA region code, derived from the area imported.",
    )

    opening_hours = models.CharField(max_length=200, blank=True)
    is_favorite = models.BooleanField(
        default=False,
        help_text="Pinned to the top of comparisons - the ones you actually use.",
    )

    first_imported_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(
        default=timezone.now,
        help_text="Last import that still found this place in OSM.",
    )

    class Meta:
        constraints = [
            # An OSM element can legitimately be two kinds at once - a fuel
            # station with a convenience store on the forecourt is tagged as
            # both - so kind is part of the identity.
            models.UniqueConstraint(
                fields=["kind", "osm_type", "osm_id"], name="uniq_place_osm_element"
            )
        ]
        indexes = [
            # The map queries a bounding box filtered by kind on every pan.
            models.Index(fields=["kind", "latitude", "longitude"],
                         name="idx_place_kind_latlng"),
            models.Index(fields=["brand", "city"], name="idx_place_brand_city"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """Readable even when OSM tagged only one of the fields, or none.

        Plenty of Philippine convenience stores are mapped with no name and no
        brand at all. Falling back to the OSM id produced list entries like
        "Convenience store 1381245374", which tells a reader nothing - the
        street or the city at least says which one it is.
        """
        if self.name and self.brand and self.brand.lower() not in self.name.lower():
            return f"{self.brand} - {self.name}"
        if self.name or self.brand:
            return self.name or self.brand

        kind = self.get_kind_display()
        where = self.street or self.city
        return f"{kind}, {where}" if where else kind

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"

    @property
    def locality(self) -> str:
        return ", ".join(part for part in (self.city, self.province) if part)

    @property
    def style(self) -> dict:
        return KIND_STYLE.get(PlaceKind(self.kind), KIND_STYLE[PlaceKind.FUEL])
