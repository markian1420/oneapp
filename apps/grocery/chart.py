"""
Geometry for the price-history line chart.

Computed on the server and rendered as inline SVG. No charting library: this is
one series of at most 90 points, and shipping a plotting runtime to draw a
polyline would cost more than the page it sits on - and would stop working on
the phone-with-no-signal case the rest of the app is built around.

The browser receives the shape, not the price table. The table is rendered
separately and deliberately, as the accessible view of the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# viewBox units. The SVG scales to its container, so these are a coordinate
# space rather than pixels.
WIDTH = 720
HEIGHT = 220
PAD_LEFT = 8
PAD_RIGHT = 8
PAD_TOP = 16
PAD_BOTTOM = 28


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    label: str
    value: Decimal
    iso: str


@dataclass
class Chart:
    points: list[Point]
    polyline: str
    area: str
    low: Decimal
    high: Decimal
    gridlines: list[dict]
    first_label: str
    last_label: str

    @property
    def has_shape(self) -> bool:
        # One point is a dot, not a trend. Two is the minimum that can slope.
        return len(self.points) >= 2


def build(series) -> Chart | None:
    """Turn oldest-first price rows into drawable geometry.

    ``series`` is an iterable of CommodityPrice ordered oldest first.
    """
    rows = list(series)
    if not rows:
        return None

    values = [row.price for row in rows]
    low, high = min(values), max(values)

    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM

    # A flat series would divide by zero and, drawn to full scale, would turn
    # rounding noise into a mountain range. Centre it instead.
    span = high - low
    flat = span == 0

    points: list[Point] = []
    for index, row in enumerate(rows):
        x = PAD_LEFT + (plot_width * index / max(len(rows) - 1, 1))
        if flat:
            y = PAD_TOP + plot_height / 2
        else:
            ratio = float((row.price - low) / span)
            y = PAD_TOP + plot_height * (1 - ratio)
        points.append(
            Point(
                x=round(x, 2),
                y=round(y, 2),
                label=row.observed_on.strftime("%d %b"),
                value=row.price,
                iso=row.observed_on.isoformat(),
            )
        )

    polyline = " ".join(f"{p.x},{p.y}" for p in points)
    baseline = HEIGHT - PAD_BOTTOM
    area = (
        f"{points[0].x},{baseline} "
        + polyline
        + f" {points[-1].x},{baseline}"
    )

    # Three recessive gridlines: the range and its midpoint. Enough to read a
    # value off, few enough to stay behind the data.
    gridlines = []
    for fraction, value in ((0.0, high), (0.5, (high + low) / 2), (1.0, low)):
        gridlines.append({
            "y": round(PAD_TOP + plot_height * fraction, 2),
            "value": value,
        })

    return Chart(
        points=points,
        polyline=polyline,
        area=area,
        low=low,
        high=high,
        gridlines=gridlines,
        first_label=points[0].label,
        last_label=points[-1].label,
    )
