"""
Every Philippine card issuer this app knows about, and what each one publishes.

The registry deliberately lists issuers that cannot be read as well as those
that can. "BDO is not in the list" and "BDO blocks automated readers" look the
same from the outside and mean very different things, so the second is written
down. `import_bank_promos --list` prints the lot.

Six issuers publish enough to import. The rest either render their listing in
JavaScript, publish no dates, or answer 403 to anything that is not a browser -
and a 403 is a decision by the site owner, so it is respected rather than
worked around.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import bankcom, bpi, eastwest, maya, metrobank, rcbc
from .base import BankPromo, get


@dataclass(frozen=True)
class Issuer:
    """One card issuer, readable or not."""

    key: str
    name: str
    url: str
    base: str = ""
    # Parses a single fetched listing. Set for issuers whose promos, dates and
    # all, fit on one page.
    reader: Callable | None = None
    # Does its own fetching, for listings that are paged or need detail pages.
    collector: Callable | None = None
    # Why there is no reader. Written from what the site actually did when
    # asked, not from assumption.
    blocked: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.reader or self.collector)


ISSUERS: tuple[Issuer, ...] = (
    # ---------------------------------------------------------------- readable
    Issuer(
        key="metrobank",
        name="Metrobank",
        url="https://www.metrobank.com.ph/promos",
        base="https://www.metrobank.com.ph",
        reader=metrobank.parse,
    ),
    Issuer(
        key="bpi",
        name="BPI",
        url="https://www.bpi.com.ph/personal/rewards-and-promotions/promos",
        base="https://www.bpi.com.ph",
        reader=bpi.parse,
    ),
    Issuer(
        key="rcbc",
        name="RCBC",
        url="https://rcbccredit.com/promos",
        base="https://rcbccredit.com",
        collector=rcbc.collect,
    ),
    Issuer(
        key="eastwest",
        name="EastWest",
        url="https://www.eastwestbanker.com/promos",
        base="https://www.eastwestbanker.com",
        collector=eastwest.collect,
    ),
    Issuer(
        key="bankcom",
        name="Bank of Commerce",
        url="https://www.bankcom.com.ph/promotions",
        base="https://www.bankcom.com.ph",
        collector=bankcom.collect,
    ),
    Issuer(
        key="maya",
        name="Maya",
        url="https://www.maya.ph/deals",
        base="https://www.maya.ph",
        reader=maya.parse,
    ),

    # ------------------------------------------------------------ not readable
    Issuer(
        key="bdo",
        name="BDO",
        url="https://www.bdo.com.ph/personal/credit-cards/promos",
        blocked="Answers HTTP 403 to any client that is not a browser. That is "
                "the site owner's decision and it is left alone.",
    ),
    Issuer(
        key="securitybank",
        name="Security Bank",
        url="https://www.securitybank.com/promos/",
        blocked="Answers HTTP 403 to any client that is not a browser, robots.txt "
                "included. Left alone for the same reason as BDO.",
    ),
    Issuer(
        key="unionbank",
        name="UnionBank",
        url="https://www.unionbankph.com/",
        blocked="No public promo listing: every usual path returns 404.",
    ),
    Issuer(
        key="chinabank",
        name="China Bank",
        url="https://www.chinabank.ph/",
        blocked="No public promo listing: every usual path returns 404.",
    ),
    Issuer(
        key="pnb",
        name="PNB",
        url="https://www.pnb.com.ph/index.php/promos",
        blocked="The page loads, but the listing is built in the browser - the "
                "HTML contains no promos and no dates.",
    ),
    Issuer(
        key="hsbc",
        name="HSBC Philippines",
        url="https://www.hsbc.com.ph/credit-cards/offers/",
        blocked="The page loads, but the offers are built in the browser - the "
                "HTML contains no promo links.",
    ),
    Issuer(
        key="aub",
        name="AUB",
        url="https://www.aub.com.ph/promos",
        blocked="Answers HTTP 200 with an empty body.",
    ),
    Issuer(
        key="landbank",
        name="Landbank",
        url="https://www.landbank.com/promos",
        blocked="The page carries no promo listing at all.",
    ),
    Issuer(
        key="maybank",
        name="Maybank Philippines",
        url="https://www.maybank.com.ph/promotions",
        blocked="The listing is still up but unmaintained - the promo pages it "
                "links to carry 2017 dates.",
    ),
    Issuer(
        key="psbank",
        name="PSBank",
        url="https://www.psbank.com.ph/promos",
        blocked="Publishes loan and raffle press releases rather than card "
                "discounts, with the dates buried in prose.",
    ),
    Issuer(
        key="homecredit",
        name="Home Credit",
        url="https://www.homecredit.ph/promos",
        blocked="The page loads but states no promo dates.",
    ),
)

BY_KEY = {issuer.key: issuer for issuer in ISSUERS}

# What can actually be imported. The command's --bank choices come from here, so
# asking for a blocked issuer fails with its reason rather than a bare KeyError.
SOURCES = {issuer.key: issuer for issuer in ISSUERS if issuer.readable}


def fetch(bank: str) -> list[BankPromo]:
    """Read one issuer's published promos."""
    issuer = BY_KEY.get(bank)
    if issuer is None:
        raise KeyError(bank)
    if not issuer.readable:
        raise ValueError(f"{issuer.name}: {issuer.blocked}")

    source = {"url": issuer.url, "base": issuer.base or issuer.url}
    if issuer.collector:
        return issuer.collector(source)
    return issuer.reader(get(issuer.url), base_url=source["base"])


__all__ = ["BY_KEY", "ISSUERS", "SOURCES", "BankPromo", "Issuer", "fetch"]
