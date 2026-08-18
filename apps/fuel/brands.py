"""
Brand normalisation for OpenStreetMap fuel stations.

OSM is crowd-tagged, so the same chain arrives as "SEAOIL", "Seaoil", "Sea Oil"
and "SEAOIL Philippines". Left alone that fragments every brand filter and
every join to a DOE advisory, which publishes one price per brand. Normalising
on import means the raw tag is still kept on the station for tracing, but
everything downstream sees one spelling.
"""

from __future__ import annotations

import re

# Canonical spellings, matched against a squashed lowercase key. Order does not
# matter; lookup is exact on the key, then a prefix scan for the long tail like
# "petron bagumbayan".
CANONICAL_BRANDS = {
    "petron": "Petron",
    "shell": "Shell",
    "pilipinasshell": "Shell",
    "caltex": "Caltex",
    "chevron": "Caltex",
    "phoenix": "Phoenix",
    "phoenixpetroleum": "Phoenix",
    "phoenixfuels": "Phoenix",
    "seaoil": "Seaoil",
    "unioil": "Unioil",
    "flyingv": "Flying V",
    "total": "TotalEnergies",
    "totalenergies": "TotalEnergies",
    "totalphilippines": "TotalEnergies",
    "ptt": "PTT",
    "pttphilippines": "PTT",
    "cleanfuel": "Cleanfuel",
    "unofuel": "Uno Fuel",
    "uno": "Uno Fuel",
    "jetti": "Jetti",
    "petrogazz": "Petro Gazz",
    "globaloil": "Global Oil",
    "insularoil": "Insular Oil",
    "cityoil": "City Oil",
    "filoil": "Filoil",
    "easternpetroleum": "Eastern Petroleum",
    "eastern": "Eastern Petroleum",
    "goldenshare": "Goldenshare",
    "sinopec": "Sinopec",
    "endless": "Endless Energy",
    "microdyne": "Microdyne",
    "rephil": "Rephil",
    "seaoilphilippines": "Seaoil",
    "petronascorporation": "Petronas",
    "petronas": "Petronas",
}

# Words that appear alongside a brand and carry no identity of their own.
_NOISE = re.compile(
    r"\b(gas(oline)?|service|filling|fuel(s)?|station|petroleum|petrol|corp(oration)?"
    r"|inc|philippines|phil|ph|co|company|energy|energies|oil\s*depot)\b",
    re.IGNORECASE,
)


def _key(value: str) -> str:
    """Squash a tag to letters and digits so spacing and case stop mattering."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalise_brand(raw: str) -> str:
    """Best canonical brand for an OSM brand/operator/name tag.

    Returns a tidied version of the input when nothing matches, rather than an
    empty string: an independent station with a real name is still worth
    showing, it just has no advisory to join to.
    """
    if not raw:
        return ""

    value = raw.strip()
    key = _key(value)
    if key in CANONICAL_BRANDS:
        return CANONICAL_BRANDS[key]

    # "Petron Ortigas Ave" or "Shell - C5" - the brand leads the string often
    # enough to be worth a prefix pass, longest key first so "seaoil" is not
    # shadowed by a shorter partial match.
    for candidate in sorted(CANONICAL_BRANDS, key=len, reverse=True):
        if key.startswith(candidate):
            return CANONICAL_BRANDS[candidate]

    stripped = _NOISE.sub(" ", value)
    stripped = re.sub(r"[\s\-_]+", " ", stripped).strip(" -,")
    return stripped[:60] or value[:60]
