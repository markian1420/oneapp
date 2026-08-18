"""
Parser for the DA Daily Price Index PDF.

The document is a three-column table - commodity, specification, price - with
no delimiter between the columns in the extracted text. Splitting the joined
line with a regex means guessing where the commodity ends, and it guesses wrong
on rows like "Chicken Breast, Local Magnolia 222.68", where the brand belongs
to the specification column. So this works from word geometry instead: every
word carries an x coordinate, and the column boundaries are real.

Verified against the published NCR editions for 14 and 17 August 2026.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .models import CommodityCategory

logger = logging.getLogger(__name__)

# Column boundaries in PDF points, from the published layout. The commodity
# column starts around x=78, specification around x=268, price around x=482;
# the midpoints between them are wide of any real word.
SPEC_COLUMN_X = 250.0
PRICE_COLUMN_X = 410.0

# Words on one table row do NOT reliably share a baseline. Editions differ:
# 17 Aug 2026 sets the price cell on the same baseline as its commodity, while
# 5 Aug 2026 sets it 3.12pt higher. Rows are pitched 20.76pt apart, so a
# tolerance of 8pt absorbs that drift with a wide margin either side and still
# cannot merge two adjacent rows.
#
# This was not theoretical: at 3.0pt the 5 August edition lost 18 of its 162
# rows, and it only surfaced because dropped rows are counted and reported
# rather than swallowed.
ROW_TOLERANCE = 8.0

# Repeating page furniture that is not data.
FURNITURE = re.compile(
    r"^(page\s+\d+\s+of\s+\d+|prevailing|unit\s*\(p/unit\)|"
    r"commodity\s+specification.*|department\s+of\s+agriculture|"
    r"daily\s+price\s+index|national\s+capital\s+region.*|"
    r"prevailing\s+retail\s+price.*|.*wet\s+markets?.*)$",
    re.IGNORECASE,
)

SECTION_HEADINGS = {
    "IMPORTED COMMERCIAL RICE": CommodityCategory.IMPORTED_RICE,
    "LOCAL COMMERCIAL RICE": CommodityCategory.LOCAL_RICE,
    "CORN PRODUCTS": CommodityCategory.CORN,
    "LEGUMES": CommodityCategory.LEGUMES,
    "FISH PRODUCTS": CommodityCategory.FISH,
    "BEEF MEAT PRODUCTS": CommodityCategory.BEEF,
    "PORK MEAT PRODUCTS": CommodityCategory.PORK,
    "OTHER LIVESTOCK MEAT PRODUCTS": CommodityCategory.OTHER_MEAT,
    "OTHER LIVESTOCK MEAT": CommodityCategory.OTHER_MEAT,
    "POULTRY PRODUCTS": CommodityCategory.POULTRY,
    "LOWLAND VEGETABLES": CommodityCategory.LOWLAND_VEG,
    "HIGHLAND VEGETABLES": CommodityCategory.HIGHLAND_VEG,
    "SPICES": CommodityCategory.SPICES,
    "FRUITS": CommodityCategory.FRUITS,
    "OTHER BASIC COMMODITIES": CommodityCategory.OTHER_BASIC,
}

# Everything below this marker is methodology prose and the list of covered
# markets, not table data.
NOTES_START = re.compile(r"^note\(s\)?:", re.IGNORECASE)

TITLE_DATE = re.compile(
    r"\(?\w+day,\s+(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})\)?"
)


@dataclass
class Row:
    """One parsed commodity line."""

    category: str
    name: str
    specification: str
    price: Decimal


@dataclass
class ParsedIndex:
    published_on: date | None = None
    region: str = "NCR"
    rows: list[Row] = field(default_factory=list)
    # Reported separately on purpose. "n/a" is the DA saying nobody had the
    # commodity that day, which is data. A dropped row is the parser failing,
    # which is a bug - collapsing the two would hide the second behind the
    # first.
    unavailable: int = 0
    dropped: int = 0


def _clean(text: str) -> str:
    """Collapse whitespace and drop the mojibake the DA's exporter emits.

    Market names arrive with a replacement character where the enye should be
    ("Mu�oz"). Nothing downstream benefits from carrying that through.
    """
    text = text.replace("�", "n").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _to_decimal(raw: str) -> Decimal | None:
    """Parse a price cell, or None when the DA reported no data.

    "n/a" is a real and frequent value - a commodity nobody had that day. It is
    not zero, and recording it as zero would drag every average down.
    """
    cleaned = raw.replace(",", "").replace("P", "").strip()
    if not cleaned or cleaned.lower() in {"n/a", "na", "-", "--"}:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _rows_from_words(words: list[dict]) -> list[tuple[str, str, str]]:
    """Group words into visual rows, then into the three columns."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= ROW_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])

    grouped = []
    for line in lines:
        left, middle, right = [], [], []
        for word in sorted(line, key=lambda w: w["x0"]):
            if word["x0"] >= PRICE_COLUMN_X:
                right.append(word["text"])
            elif word["x0"] >= SPEC_COLUMN_X:
                middle.append(word["text"])
            else:
                left.append(word["text"])
        grouped.append((_clean(" ".join(left)),
                        _clean(" ".join(middle)),
                        _clean(" ".join(right))))
    return grouped


def parse(pdf) -> ParsedIndex:
    """Parse an opened pdfplumber document into commodity rows.

    Two wrapping behaviours have to be handled, both seen in the published
    editions. A specification too long for its cell wraps onto the line above
    its price. Worse, a long commodity name wraps *around* the priced line -
    "Cooking Oil (Palm Olein, Jolly" sits above the row carrying 154.00, and
    "Brand)" sits below it - so a row's name can be completed by a line that
    has not been read yet.
    """
    result = ParsedIndex()
    category = CommodityCategory.OTHER
    pending_name = ""
    pending_spec = ""

    for page in pdf.pages:
        rows = _rows_from_words(page.extract_words())
        # Lines pulled forward by the look-ahead below. Without this they are
        # read a second time on their own iteration and leak onto the next
        # commodity.
        consumed: set[int] = set()

        for index, (name, spec, price_text) in enumerate(rows):
            if index in consumed:
                continue
            joined = " ".join(part for part in (name, spec, price_text) if part)

            if result.published_on is None:
                match = TITLE_DATE.search(joined)
                if match:
                    try:
                        result.published_on = datetime.strptime(
                            f"{match['month']} {match['day']} {match['year']}",
                            "%B %d %Y",
                        ).date()
                    except ValueError:
                        logger.warning("Unparseable date in title: %s", joined)

            # Everything from "Note(s):" to the end of the document is the
            # methodology footer and the list of covered markets. Parsing on
            # would turn prose into commodities.
            if NOTES_START.match(joined):
                return result

            if not joined or FURNITURE.match(joined):
                continue

            heading = name.upper().strip()
            if heading in SECTION_HEADINGS and not price_text:
                category = SECTION_HEADINGS[heading]
                pending_name = pending_spec = ""
                continue

            if not price_text:
                # No number here, so this is a wrapped cell held for the next
                # priced row.
                if name:
                    pending_name = f"{pending_name} {name}".strip()
                if spec:
                    pending_spec = f"{pending_spec} {spec}".strip()
                continue

            price = _to_decimal(price_text)
            full_name = _clean(f"{pending_name} {name}")
            full_spec = _clean(f"{pending_spec} {spec}")
            pending_name = pending_spec = ""

            if price is None:
                # The DA had no price for this commodity today.
                result.unavailable += 1
                continue

            # A name that wrapped past its own price finishes on the following
            # line. Only a bare name with nothing else on it qualifies, so a
            # real commodity row is never swallowed.
            if index + 1 < len(rows):
                next_name, next_spec, next_price = rows[index + 1]
                looks_like_a_tail = bool(next_name) and (
                    # A continuation either closes a bracket the name opened,
                    # or starts lower-case because it is mid-phrase. A new
                    # commodity does neither.
                    (next_name.endswith(")") and full_name.count("(") > full_name.count(")"))
                    or next_name[:1].islower()
                )
                if (
                    looks_like_a_tail
                    and not next_spec
                    and not next_price
                    and next_name.upper() not in SECTION_HEADINGS
                    and not FURNITURE.match(next_name)
                    and not NOTES_START.match(next_name)
                ):
                    full_name = _clean(f"{full_name} {next_name}")
                    consumed.add(index + 1)

            if not full_name and not full_spec:
                result.dropped += 1
                logger.warning("Orphan price %s with no commodity", price)
                continue

            result.rows.append(
                Row(
                    category=category,
                    name=(full_name or full_spec)[:160],
                    specification=(full_spec if full_name else "")[:160],
                    price=price,
                )
            )

    return result


def daily_index_url(when: date) -> str:
    """The published location for one day's index.

    The DA files these under the month they were published in, named for the
    date they cover, e.g.
    /wp-content/uploads/2026/08/Daily-Price-Index-August-17-2026.pdf
    """
    return (
        f"https://www.da.gov.ph/wp-content/uploads/{when:%Y}/{when:%m}/"
        f"Daily-Price-Index-{when:%B}-{when.day}-{when:%Y}.pdf"
    )
