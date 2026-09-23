"""
Overpass API client for pulling Philippine places out of OpenStreetMap.

Places are fetched one administrative area at a time rather than as one
national query. Two reasons: the public Overpass instances reject a
country-wide query for anything but a bare count when they are busy, and asking
for a known area means the province and DOE region come from the area we asked
for instead of from an address tag that only 13% of Philippine stations carry.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from django.conf import settings

from .regions import PROVINCE_TO_REGION, NCR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Area:
    """One administrative unit to import, and what it implies about location."""

    code: str
    name: str
    region: str
    # Overpass area ids are the OSM relation id plus 3600000000.
    relation_id: int | None = None
    # south, west, north, east. Set only for an ad-hoc --bbox import, where
    # there is no administrative boundary to name.
    bbox: tuple[float, float, float, float] | None = None

    @property
    def is_bbox(self) -> bool:
        return self.bbox is not None

    @property
    def selector(self) -> str:
        if self.is_bbox:
            return ""
        if self.relation_id:
            return f"area({3600000000 + self.relation_id})->.searchArea;"
        return (
            f'area["boundary"="administrative"]["ISO3166-2"="{self.code}"]'
            f"->.searchArea;"
        )

    @property
    def filter_clause(self) -> str:
        if self.is_bbox:
            return "({},{},{},{})".format(*self.bbox)
        return "(area.searchArea)"

    @property
    def province(self) -> str:
        # Metro Manila is a region, not a province, and a bounding box is not
        # an administrative unit at all. Blank in both cases is more honest
        # than writing something that is not a province into a province field.
        return "" if self.code in {"PH-00", "BBOX"} else self.name


def bbox_area(raw: str) -> Area:
    """Build an ad-hoc area from a 'south,west,north,east' argument.

    Province and region are left blank rather than guessed: a box drawn on a
    map does not respect a provincial boundary, and a wrong region silently
    attaches the wrong DOE advisory to every station in it.
    """
    parts = [piece.strip() for piece in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox needs four numbers: south,west,north,east")

    try:
        south, west, north, east = (float(piece) for piece in parts)
    except ValueError as exc:
        raise ValueError("--bbox values must be numbers") from exc

    if south >= north or west >= east:
        raise ValueError("--bbox must be south,west,north,east with south < north")

    return Area(
        code="BBOX",
        name=f"box {south},{west} to {north},{east}",
        region="",
        bbox=(south, west, north, east),
    )


# Metro Manila is admin_level 3 and carries ISO PH-00; the 82 provinces are
# admin_level 4. Verified against Overpass rather than assumed - the province
# list moved when Maguindanao split in 2022 and Davao Occidental was created.
METRO_MANILA = Area(code="PH-00", name="Metro Manila", region=NCR, relation_id=147488)

PROVINCE_ISO = {
    "PH-ABR": "Abra", "PH-AGN": "Agusan del Norte", "PH-AGS": "Agusan del Sur",
    "PH-AKL": "Aklan", "PH-ALB": "Albay", "PH-ANT": "Antique",
    "PH-APA": "Apayao", "PH-AUR": "Aurora", "PH-BAN": "Bataan",
    "PH-BAS": "Basilan", "PH-BEN": "Benguet", "PH-BIL": "Biliran",
    "PH-BOH": "Bohol", "PH-BTG": "Batangas", "PH-BTN": "Batanes",
    "PH-BUK": "Bukidnon", "PH-BUL": "Bulacan", "PH-CAG": "Cagayan",
    "PH-CAM": "Camiguin", "PH-CAN": "Camarines Norte", "PH-CAP": "Capiz",
    "PH-CAS": "Camarines Sur", "PH-CAT": "Catanduanes", "PH-CAV": "Cavite",
    "PH-CEB": "Cebu", "PH-COM": "Davao de Oro", "PH-DAO": "Davao Oriental",
    "PH-DAS": "Davao del Sur", "PH-DAV": "Davao del Norte",
    "PH-DIN": "Dinagat Islands", "PH-DVO": "Davao Occidental",
    "PH-EAS": "Eastern Samar", "PH-GUI": "Guimaras", "PH-IFU": "Ifugao",
    "PH-ILI": "Iloilo", "PH-ILN": "Ilocos Norte", "PH-ILS": "Ilocos Sur",
    "PH-ISA": "Isabela", "PH-KAL": "Kalinga", "PH-LAG": "Laguna",
    "PH-LAN": "Lanao del Norte", "PH-LAS": "Lanao del Sur", "PH-LEY": "Leyte",
    "PH-LUN": "La Union", "PH-MAD": "Marinduque", "PH-MAS": "Masbate",
    "PH-MDC": "Occidental Mindoro", "PH-MDR": "Oriental Mindoro",
    "PH-MGN": "Maguindanao del Norte", "PH-MGS": "Maguindanao del Sur",
    "PH-MOU": "Mountain Province", "PH-MSC": "Misamis Occidental",
    "PH-MSR": "Misamis Oriental", "PH-NCO": "Cotabato",
    "PH-NEC": "Negros Occidental", "PH-NER": "Negros Oriental",
    "PH-NSA": "Northern Samar", "PH-NUE": "Nueva Ecija",
    "PH-NUV": "Nueva Vizcaya", "PH-PAM": "Pampanga", "PH-PAN": "Pangasinan",
    "PH-PLW": "Palawan", "PH-QUE": "Quezon", "PH-QUI": "Quirino",
    "PH-RIZ": "Rizal", "PH-ROM": "Romblon", "PH-SAR": "Sarangani",
    "PH-SCO": "South Cotabato", "PH-SIG": "Siquijor",
    "PH-SLE": "Southern Leyte", "PH-SLU": "Sulu", "PH-SOR": "Sorsogon",
    "PH-SUK": "Sultan Kudarat", "PH-SUN": "Surigao del Norte",
    "PH-SUR": "Surigao del Sur", "PH-TAR": "Tarlac", "PH-TAW": "Tawi-Tawi",
    "PH-WSA": "Samar", "PH-ZAN": "Zamboanga del Norte",
    "PH-ZAS": "Zamboanga del Sur", "PH-ZMB": "Zambales",
    "PH-ZSI": "Zamboanga Sibugay",
}


def all_areas() -> list[Area]:
    """Metro Manila first, then every province alphabetically."""
    provinces = [
        Area(code=code, name=name, region=PROVINCE_TO_REGION.get(name.lower(), ""))
        for code, name in sorted(PROVINCE_ISO.items(), key=lambda kv: kv[1])
    ]
    return [METRO_MANILA, *provinces]


def resolve_areas(names: list[str]) -> list[Area]:
    """Turn command-line area arguments into Area objects.

    Accepts an ISO code (PH-RIZ), a province name (Rizal), NCR, or 'all'.
    """
    if not names or any(n.lower() == "all" for n in names):
        return all_areas()

    by_code = {a.code: a for a in all_areas()}
    by_name = {a.name.lower(): a for a in all_areas()}
    aliases = {"ncr": METRO_MANILA, "metro manila": METRO_MANILA,
               "manila": METRO_MANILA, "ph-00": METRO_MANILA}

    resolved: list[Area] = []
    unknown: list[str] = []
    for raw in names:
        key = raw.strip()
        area = (
            by_code.get(key.upper())
            or by_name.get(key.lower())
            or aliases.get(key.lower())
        )
        if area:
            resolved.append(area)
        else:
            unknown.append(raw)

    if unknown:
        raise ValueError(
            "Unknown area(s): " + ", ".join(unknown) + ". Use an ISO code such as "
            "PH-RIZ, a province name, NCR, or 'all'."
        )
    return resolved


class OverpassError(RuntimeError):
    pass


# Which OSM tags identify each kind of place. A kind can need more than one
# selector: OSM separates a mall from a department store, but for deciding
# where to shop that is a distinction without a difference.
KIND_SELECTORS = {
    "fuel": [('"amenity"="fuel"',)],
    "market": [('"amenity"="marketplace"',)],
    "supermarket": [('"shop"="supermarket"',)],
    "convenience": [('"shop"="convenience"',)],
    "fast_food": [('"amenity"="fast_food"',)],
    "restaurant": [('"amenity"="restaurant"',)],
    "mall": [('"shop"="mall"',), ('"shop"="department_store"',)],
    "clothes": [('"shop"="clothes"',), ('"shop"="shoes"',)],
    "pharmacy": [('"amenity"="pharmacy"',)],
}


def build_query(area: Area, kind: str) -> str:
    """Every place of one kind in one area, however it happens to be mapped.

    All three element types, because OSM uses all three for the same thing: a
    station is a node when someone dropped a pin, a way when someone traced
    the forecourt, and a relation when the forecourt has a hole in it or the
    shop and canopy were mapped as separate rings. Asking only for nodes and
    ways silently loses the third kind - the Petron across from Estancia Mall
    is relation 12568831 - and nothing downstream reveals the gap, because
    what was never fetched cannot be reported missing.

    'out center' collapses any of them to a single point, which is what the map
    needs - a forecourt or a mall footprint is not more useful than a pin and
    costs far more to ship to the browser.
    """
    selectors = KIND_SELECTORS.get(kind)
    if not selectors:
        raise ValueError(f"No OSM selector defined for kind {kind!r}")

    where = area.filter_clause
    clauses = []
    for selector in selectors:
        tags = "".join(f"[{part}]" for part in selector)
        clauses.append(f'  node{tags}{where};')
        clauses.append(f'  way{tags}{where};')
        clauses.append(f'  relation{tags}{where};')

    body = "\n".join(clauses)
    return f"""
[out:json][timeout:{settings.OVERPASS_TIMEOUT}];
{area.selector}
(
{body}
);
out tags center;
""".strip()


def fetch_area(area: Area, kind: str, *, max_attempts: int = 5) -> list[dict]:
    """Run one area query, moving down the endpoint list on failure.

    The public instances are shared and frequently loaded. They signal it three
    different ways - a 429, a 504, or a 200 carrying an HTML error page - so a
    status check alone is not enough: the body has to parse as JSON before the
    response counts as a success.
    """
    endpoints = list(settings.OVERPASS_ENDPOINTS)
    last_error = ""

    for attempt in range(max_attempts):
        endpoint = endpoints[attempt % len(endpoints)]
        if attempt:
            # Exponential-ish backoff, capped. When Overpass says it is busy it
            # means it; retrying hard is how an IP gets blocked rather than
            # served, and the whole run is unattended anyway.
            time.sleep(min(60, 10 * (2 ** (attempt - 1))))

        try:
            response = httpx.post(
                endpoint,
                data={"data": build_query(area, kind)},
                timeout=settings.OVERPASS_TIMEOUT + 30,
                headers={"User-Agent": "OneApp/1.0 (personal fuel price tracker)"},
            )
        except httpx.HTTPError as exc:
            last_error = f"{endpoint}: {exc}"
            logger.warning("Overpass request failed (%s), retrying", last_error)
            continue

        if response.status_code != 200:
            last_error = f"{endpoint}: HTTP {response.status_code}"
            logger.warning("Overpass returned %s, retrying", last_error)
            continue

        try:
            payload = response.json()
        except ValueError:
            last_error = f"{endpoint}: non-JSON body (server busy)"
            logger.warning("Overpass sent an error page, retrying")
            continue

        return payload.get("elements", [])

    raise OverpassError(
        f"Overpass would not answer for {kind} in {area.name} after "
        f"{max_attempts} attempts. "
        f"Last error: {last_error}"
    )


def element_coordinates(element: dict) -> tuple[float, float] | None:
    """Latitude and longitude: a node's own, or a way or relation's centre."""
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center:
        return float(center["lat"]), float(center["lon"])
    return None
