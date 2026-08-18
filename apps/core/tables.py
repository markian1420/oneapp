"""
Server-side sorting, paging and page sizing for OneApp tables.

Everything happens in the database: the browser receives one page of rows and
nothing more. That is not only faster, it is the only way to keep unauthorised
rows out of the client — a table that ships every row and hides some with
JavaScript has already leaked them.

The sort parameter is resolved through an explicit allowlist. A column name from
the query string is never handed to ``order_by``: besides the injection surface,
Django would happily follow relations, so ``?sort=api_key__client__name`` on the
wrong table becomes an information leak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.core.paginator import EmptyPage, InvalidPage, Paginator

PAGE_SIZE_CHOICES = (10, 25, 50, 100, 200)
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200

# Query-string keys the table itself owns.
SORT_PARAM = "sort"
DIR_PARAM = "dir"
PER_PAGE_PARAM = "per_page"
PAGE_PARAM = "page"


@dataclass(frozen=True)
class Column:
    """One table column.

    ``order_by`` holds the ORM field names to sort by, and is the *only* place
    a sortable column's database fields are defined. A column with no
    ``order_by`` simply is not sortable — there is no way to make it so from
    the query string.
    """

    key: str
    label: str
    order_by: tuple[str, ...] = ()
    align: str = "left"
    css: str = ""
    # Some columns are computed in Python (a property, an aggregate we do not
    # annotate) and cannot be sorted in SQL. Saying so is better than sorting
    # one page and calling it sorted.
    note: str = ""

    @property
    def sortable(self) -> bool:
        return bool(self.order_by)


@dataclass
class Header:
    column: Column
    label: str
    sortable: bool
    active: bool
    descending: bool
    url: str
    align: str
    css: str
    note: str


@dataclass
class Table:
    """Everything a template needs to render a server-side table."""

    page: object
    headers: list[Header] = field(default_factory=list)
    per_page: int = DEFAULT_PAGE_SIZE
    page_sizes: tuple[int, ...] = PAGE_SIZE_CHOICES
    sort_key: str = ""
    descending: bool = False
    base_params: dict = field(default_factory=dict)
    total: int = 0

    # ------------------------------------------------------------------

    def _query(self, **overrides) -> str:
        params = dict(self.base_params)
        params.update(overrides)
        cleaned = {k: v for k, v in params.items() if v not in (None, "")}
        return "?" + urlencode(cleaned, doseq=True) if cleaned else ""

    def page_size_url(self, size: int) -> str:
        # Changing page size always returns to page 1: staying on page 9 of a
        # 25-row listing is meaningless once rows are 200 to a page.
        return self._query(**{PER_PAGE_PARAM: size, PAGE_PARAM: None})

    def page_url(self, number: int) -> str:
        return self._query(**{PAGE_PARAM: number})

    @property
    def page_size_options(self) -> list[dict]:
        return [
            {"size": size, "url": self.page_size_url(size),
             "active": size == self.per_page}
            for size in self.page_sizes
        ]

    @property
    def elided_pages(self) -> list[dict]:
        """Page links with ellipses, so 400 pages do not render 400 links.

        URLs are built here rather than in the template: a template filter
        taking both a page number and the table would be doing Python's job
        badly.
        """
        paginator = self.page.paginator
        try:
            numbers = paginator.get_elided_page_range(
                self.page.number, on_each_side=1, on_ends=1
            )
        except (EmptyPage, InvalidPage):
            numbers = paginator.page_range

        items = []
        for number in numbers:
            if number == paginator.ELLIPSIS:
                items.append({"ellipsis": True, "label": str(number)})
            else:
                items.append({
                    "ellipsis": False,
                    "label": str(number),
                    "number": number,
                    "url": self.page_url(number),
                    "current": number == self.page.number,
                })
        return items

    @property
    def has_pages(self) -> bool:
        return self.page.paginator.num_pages > 1

    @property
    def previous_url(self) -> str:
        return self.page_url(self.page.previous_page_number()) \
            if self.page.has_previous() else ""

    @property
    def next_url(self) -> str:
        return self.page_url(self.page.next_page_number()) \
            if self.page.has_next() else ""

    @property
    def first_url(self) -> str:
        return self.page_url(1)

    @property
    def last_url(self) -> str:
        return self.page_url(self.page.paginator.num_pages)


def resolve_per_page(request) -> int:
    raw = request.GET.get(PER_PAGE_PARAM)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if value in PAGE_SIZE_CHOICES:
        return value
    # Accept an arbitrary number but never an unbounded one; a request for
    # 100000 rows is either a mistake or an attempt to exhaust the worker.
    return max(1, min(value, MAX_PAGE_SIZE))


def build_table(request, queryset, columns: list[Column], *,
                default_sort: str = "", default_desc: bool = False,
                tiebreak: str = "pk", preserve: tuple[str, ...] = ()) -> Table:
    """Sort, page and describe a queryset for rendering.

    ``preserve`` names extra query parameters (a search term, a filter) that
    every generated link should carry, so sorting does not silently discard the
    filter the operator applied.
    """
    requested = request.GET.get(SORT_PARAM, "") or default_sort
    descending = (
        request.GET.get(DIR_PARAM, "").lower() == "desc"
        if request.GET.get(DIR_PARAM)
        else (default_desc and requested == default_sort)
    )

    by_key = {c.key: c for c in columns}
    column = by_key.get(requested)
    if column is None or not column.sortable:
        column = by_key.get(default_sort)
        descending = default_desc
    sort_key = column.key if column else ""

    if column and column.sortable:
        prefix = "-" if descending else ""
        ordering = [f"{prefix}{f}" for f in column.order_by]
    else:
        ordering = []

    # A deterministic tiebreaker: without one, rows with equal sort values can
    # swap between pages and the operator sees duplicates or gaps.
    if tiebreak:
        ordering.append(tiebreak)
    queryset = queryset.order_by(*ordering) if ordering else queryset

    per_page = resolve_per_page(request)
    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(request.GET.get(PAGE_PARAM))

    base_params = {
        key: request.GET.get(key) for key in preserve if request.GET.get(key)
    }
    base_params[PER_PAGE_PARAM] = per_page
    if sort_key:
        base_params[SORT_PARAM] = sort_key
        base_params[DIR_PARAM] = "desc" if descending else "asc"

    headers = []
    for candidate in columns:
        active = candidate.key == sort_key
        # Clicking the active column flips direction; a new column starts ascending.
        next_desc = not descending if active else False
        link_params = dict(base_params)
        link_params[SORT_PARAM] = candidate.key
        link_params[DIR_PARAM] = "desc" if next_desc else "asc"
        link_params.pop(PAGE_PARAM, None)
        cleaned = {k: v for k, v in link_params.items() if v not in (None, "")}

        headers.append(Header(
            column=candidate,
            label=candidate.label,
            sortable=candidate.sortable,
            active=active,
            descending=descending and active,
            url="?" + urlencode(cleaned, doseq=True) if candidate.sortable else "",
            align=candidate.align,
            css=candidate.css,
            note=candidate.note,
        ))

    return Table(
        page=page, headers=headers, per_page=per_page, sort_key=sort_key,
        descending=descending, base_params=base_params,
        total=paginator.count,
    )


def build_list_table(request, rows: list, columns: list[Column], *,
                     default_sort: str = "", default_desc: bool = False,
                     key_of=None, preserve: tuple[str, ...] = ()) -> Table:
    """Same contract for data that is not a queryset.

    Used by the discovery screens, where rows come back from Graph or
    INFORMATION_SCHEMA rather than a local table. Sorting happens in Python
    because there is no database to push it to — still server-side, still one
    page delivered to the browser.
    """
    by_key = {c.key: c for c in columns}
    requested = request.GET.get(SORT_PARAM, "") or default_sort
    column = by_key.get(requested)
    if column is None or not column.sortable:
        column = by_key.get(default_sort)
    sort_key = column.key if column else ""

    descending = (
        request.GET.get(DIR_PARAM, "").lower() == "desc"
        if request.GET.get(DIR_PARAM)
        else (default_desc and sort_key == default_sort)
    )

    if column and column.sortable:
        field_name = column.order_by[0]
        accessor = key_of or (lambda row, name: row.get(name))

        def sort_value(row):
            value = accessor(row, field_name)
            # None sorts last either way rather than raising on mixed types.
            return (value is None, str(value).lower() if value is not None else "")

        rows = sorted(rows, key=sort_value, reverse=descending)

    per_page = resolve_per_page(request)
    paginator = Paginator(rows, per_page)
    page = paginator.get_page(request.GET.get(PAGE_PARAM))

    base_params = {
        key: request.GET.get(key) for key in preserve if request.GET.get(key)
    }
    base_params[PER_PAGE_PARAM] = per_page
    if sort_key:
        base_params[SORT_PARAM] = sort_key
        base_params[DIR_PARAM] = "desc" if descending else "asc"

    headers = []
    for candidate in columns:
        active = candidate.key == sort_key
        next_desc = not descending if active else False
        link_params = dict(base_params)
        link_params[SORT_PARAM] = candidate.key
        link_params[DIR_PARAM] = "desc" if next_desc else "asc"
        link_params.pop(PAGE_PARAM, None)
        cleaned = {k: v for k, v in link_params.items() if v not in (None, "")}
        headers.append(Header(
            column=candidate, label=candidate.label, sortable=candidate.sortable,
            active=active, descending=descending and active,
            url="?" + urlencode(cleaned, doseq=True) if candidate.sortable else "",
            align=candidate.align, css=candidate.css, note=candidate.note,
        ))

    return Table(
        page=page, headers=headers, per_page=per_page, sort_key=sort_key,
        descending=descending, base_params=base_params, total=paginator.count,
    )
