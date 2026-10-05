"""Metric tools — mock vendor performance data (simulates Ripple/internal metrics API).

In production, these would call actual internal APIs (Ripple, SPS, etc.).
For demo, returns hardcoded data for Toshiba HL Japan vendor.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Contributor:
    name: str
    value: float
    contribution_pct: float
    trend: str


@dataclass
class MetricResult:
    metric_name: str
    display_name: str
    value: float
    unit: str
    goal: float
    goal_type: str  # higher_better | lower_better
    period: str
    trend_pct: float
    trend_direction: str  # up | down | flat
    contributors_top: list[Contributor] = field(default_factory=list)
    contributors_bottom: list[Contributor] = field(default_factory=list)


_METRICS: dict[str, dict[str, MetricResult]] = {
    "toshiba_hl": {
        "net_ppm": MetricResult(
            metric_name="net_ppm",
            display_name="Net PPM",
            value=28.5,
            unit="%",
            goal=35.0,
            goal_type="higher_better",
            period="QTD",
            trend_pct=-2.3,
            trend_direction="down",
            contributors_top=[
                Contributor("Consumer Electronics", 32.1, 45.0, "up"),
                Contributor("Home Appliances", 30.2, 25.0, "flat"),
            ],
            contributors_bottom=[
                Contributor("PC Accessories", 18.3, -8.0, "down"),
                Contributor("Storage Devices", 22.0, 12.0, "down"),
            ],
        ),
        "revenue": MetricResult(
            metric_name="revenue",
            display_name="Shipped Revenue",
            value=4.2,
            unit="M USD",
            goal=5.0,
            goal_type="higher_better",
            period="QTD",
            trend_pct=-5.1,
            trend_direction="down",
            contributors_top=[
                Contributor("Consumer Electronics", 2.1, 50.0, "up"),
                Contributor("Home Appliances", 1.2, 28.0, "flat"),
            ],
            contributors_bottom=[
                Contributor("PC Accessories", 0.3, -8.0, "down"),
                Contributor("Storage Devices", 0.6, 12.0, "down"),
            ],
        ),
        "cr": MetricResult(
            metric_name="cr",
            display_name="Conversion Rate",
            value=8.2,
            unit="%",
            goal=10.0,
            goal_type="higher_better",
            period="QTD",
            trend_pct=-1.5,
            trend_direction="down",
            contributors_top=[
                Contributor("Detail Page Views", 9.5, 40.0, "up"),
                Contributor("Buy Box Win Rate", 7.8, -5.0, "down"),
            ],
            contributors_bottom=[
                Contributor("Add to Cart", 6.1, -8.0, "down"),
            ],
        ),
        "in_stock_rate": MetricResult(
            metric_name="in_stock_rate",
            display_name="In-Stock Rate",
            value=94.2,
            unit="%",
            goal=97.0,
            goal_type="higher_better",
            period="QTD",
            trend_pct=-1.2,
            trend_direction="down",
            contributors_top=[
                Contributor("Consumer Electronics", 96.8, 30.0, "up"),
                Contributor("Home Appliances", 95.5, 25.0, "flat"),
            ],
            contributors_bottom=[
                Contributor("PC Accessories", 89.1, -8.0, "down"),
                Contributor("Storage Devices", 91.0, 12.0, "down"),
            ],
        ),
        "glance_views": MetricResult(
            metric_name="glance_views",
            display_name="Glance Views",
            value=1.8,
            unit="M",
            goal=2.2,
            goal_type="higher_better",
            period="QTD",
            trend_pct=-8.2,
            trend_direction="down",
            contributors_top=[
                Contributor("Organic Search", 0.9, 50.0, "down"),
                Contributor("Advertising", 0.6, 33.0, "down"),
            ],
            contributors_bottom=[
                Contributor("Brand Store", 0.15, -5.0, "flat"),
                Contributor("Off-Amazon", 0.1, 12.0, "down"),
            ],
        ),
    },
}


def get_metric(vendor_id: str, metric_name: str, period: str = "QTD") -> Optional[MetricResult]:
    vendor = _METRICS.get(vendor_id, {})
    metric = vendor.get(metric_name)
    if metric:
        metric.period = period
    return metric


def list_available_metrics() -> list[str]:
    vendor = _METRICS.get("toshiba_hl", {})
    return list(vendor.keys())


def get_all_metrics(vendor_id: str, period: str = "QTD") -> list[MetricResult]:
    vendor = _METRICS.get(vendor_id, {})
    metrics = list(vendor.values())
    for m in metrics:
        m.period = period
    return metrics


def get_scorecard_summary(vendor_id: str, period: str = "QTD") -> dict:
    metrics = get_all_metrics(vendor_id, period)
    from backend.rag.generator import _status_label

    pillars = {}
    off_track = 0
    for m in metrics:
        status, level = _status_label(m)
        is_off = status not in ("On Track",)
        if is_off:
            off_track += 1

        pillar = "Growth" if m.metric_name in ("revenue", "glance_views") else "Operations"
        pillars.setdefault(pillar, []).append({
            "metric_name": m.metric_name,
            "display_name": m.display_name,
            "value": m.value,
            "unit": m.unit,
            "goal": m.goal,
            "status": status,
            "alert_level": level,
            "is_off_track": is_off,
        })

    return {
        "vendor_id": vendor_id,
        "period": period,
        "total_metrics": len(metrics),
        "off_track_count": off_track,
        "on_track_count": len(metrics) - off_track,
        "pillars": pillars,
    }
