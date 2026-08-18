"""
Province to DOE region mapping.

The DOE publishes retail prices per administrative region, while OSM tags
stations with a province (or, in Metro Manila, with a city). Stations therefore
carry a derived region code so an advisory row can be found without a spatial
query at read time.
"""

from __future__ import annotations

NCR = "NCR"

REGION_NAMES = {
    NCR: "National Capital Region",
    "CAR": "Cordillera Administrative Region",
    "I": "Ilocos Region",
    "II": "Cagayan Valley",
    "III": "Central Luzon",
    "IV-A": "CALABARZON",
    "IV-B": "MIMAROPA",
    "V": "Bicol Region",
    "VI": "Western Visayas",
    "VII": "Central Visayas",
    "VIII": "Eastern Visayas",
    "IX": "Zamboanga Peninsula",
    "X": "Northern Mindanao",
    "XI": "Davao Region",
    "XII": "SOCCSKSARGEN",
    "XIII": "Caraga",
    "BARMM": "Bangsamoro",
}

PROVINCE_TO_REGION = {
    # National Capital Region - tagged as a province in some OSM extracts and
    # omitted entirely in others, so the city fallback below matters here.
    "metro manila": NCR,
    "national capital region": NCR,
    "ncr": NCR,
    # Cordillera
    "abra": "CAR", "apayao": "CAR", "benguet": "CAR", "ifugao": "CAR",
    "kalinga": "CAR", "mountain province": "CAR",
    # Ilocos
    "ilocos norte": "I", "ilocos sur": "I", "la union": "I", "pangasinan": "I",
    # Cagayan Valley
    "batanes": "II", "cagayan": "II", "isabela": "II", "nueva vizcaya": "II",
    "quirino": "II",
    # Central Luzon
    "aurora": "III", "bataan": "III", "bulacan": "III", "nueva ecija": "III",
    "pampanga": "III", "tarlac": "III", "zambales": "III",
    # CALABARZON
    "batangas": "IV-A", "cavite": "IV-A", "laguna": "IV-A", "quezon": "IV-A",
    "rizal": "IV-A",
    # MIMAROPA
    "marinduque": "IV-B", "occidental mindoro": "IV-B",
    "oriental mindoro": "IV-B", "palawan": "IV-B", "romblon": "IV-B",
    # Bicol
    "albay": "V", "camarines norte": "V", "camarines sur": "V",
    "catanduanes": "V", "masbate": "V", "sorsogon": "V",
    # Western Visayas
    "aklan": "VI", "antique": "VI", "capiz": "VI", "guimaras": "VI",
    "iloilo": "VI", "negros occidental": "VI",
    # Central Visayas
    "bohol": "VII", "cebu": "VII", "negros oriental": "VII", "siquijor": "VII",
    # Eastern Visayas
    "biliran": "VIII", "eastern samar": "VIII", "leyte": "VIII",
    "northern samar": "VIII", "samar": "VIII", "western samar": "VIII",
    "southern leyte": "VIII",
    # Zamboanga Peninsula
    "zamboanga del norte": "IX", "zamboanga del sur": "IX",
    "zamboanga sibugay": "IX",
    # Northern Mindanao
    "bukidnon": "X", "camiguin": "X", "lanao del norte": "X",
    "misamis occidental": "X", "misamis oriental": "X",
    # Davao
    "davao de oro": "XI", "compostela valley": "XI", "davao del norte": "XI",
    "davao del sur": "XI", "davao occidental": "XI", "davao oriental": "XI",
    # SOCCSKSARGEN
    "cotabato": "XII", "north cotabato": "XII", "sarangani": "XII",
    "south cotabato": "XII", "sultan kudarat": "XII",
    # Caraga
    "agusan del norte": "XIII", "agusan del sur": "XIII",
    "dinagat islands": "XIII", "surigao del norte": "XIII",
    "surigao del sur": "XIII",
    # Bangsamoro
    "basilan": "BARMM", "lanao del sur": "BARMM", "maguindanao": "BARMM",
    "maguindanao del norte": "BARMM", "maguindanao del sur": "BARMM",
    "sulu": "BARMM", "tawi-tawi": "BARMM",
}

# The 16 cities and one municipality of Metro Manila. OSM frequently tags these
# with no province at all, so without this every NCR station would fall through
# to a blank region and lose its advisory baseline.
NCR_CITIES = {
    "caloocan", "las pinas", "las piñas", "makati", "malabon", "mandaluyong",
    "manila", "marikina", "muntinlupa", "navotas", "paranaque", "parañaque",
    "pasay", "pasig", "pateros", "quezon city", "san juan", "taguig",
    "valenzuela",
}


def region_for(province: str = "", city: str = "") -> str:
    """DOE region code for a station, or an empty string when unknown.

    Province wins when present; the NCR city list is the fallback for the one
    part of the country where the province tag is routinely missing.
    """
    key = (province or "").strip().lower()
    if key:
        key = key.removeprefix("province of ").strip()
        if key in PROVINCE_TO_REGION:
            return PROVINCE_TO_REGION[key]

    town = (city or "").strip().lower()
    town = town.removesuffix(" city").strip() if town != "quezon city" else town
    if town in NCR_CITIES or f"{town} city" in NCR_CITIES:
        return NCR

    return ""
