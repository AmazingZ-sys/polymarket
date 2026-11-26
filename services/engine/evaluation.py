"""Research metrics with explicit missing denominators and grouped labels."""

from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from math import log
from random import Random


def calibration(observations: list[tuple[float, int]], bins: int = 10) -> dict:
    n = len(observations)
    if not n:
        return {"samples": 0, "brier": None, "log_loss": None, "ece": None, "reliability": []}
    buckets: dict[int, list] = defaultdict(list)
    brier = cross_entropy = 0.0
    for p, y in observations:
        if not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError("Invalid prediction")
        buckets[min(int(p * bins), bins - 1)].append((p, y))
        brier += (p - y) ** 2
        bounded = max(1e-15, min(1 - 1e-15, p))
        cross_entropy -= log(bounded if y else 1 - bounded)
    curve, ece = [], 0.0
    for i, points in sorted(buckets.items()):
        count = len(points)
        predicted = sum(p for p, _ in points) / count
        frequency = sum(y for _, y in points) / count
        curve.append({"bin": i, "count": count, "probability": predicted, "frequency": frequency})
        ece += abs(predicted - frequency) * count / n
    return {"samples": n, "brier": brier / n, "log_loss": cross_entropy / n, "ece": ece, "reliability": curve}


def daily_block_interval(samples: list[tuple[datetime, Decimal]], seed: int) -> list[float] | None:
    days: dict[str, list[Decimal]] = defaultdict(list)
    for ts, amount in samples:
        days[ts.date().isoformat()].append(amount)
    groups = list(days.values())
    if len(groups) < 10:
        return None
    rng = Random(seed)
    estimates = []
    for _ in range(1000):
        draws = [v for g in rng.choices(groups, k=len(groups)) for v in g]
        estimates.append(float(sum(draws) / len(draws)))
    estimates.sort()
    return [estimates[24], estimates[974]]


def time_weighted_mean(
    samples: list[tuple[datetime, Decimal]], start: datetime, end: datetime, max_gap_seconds: int
) -> Decimal | None:
    """Piecewise-constant research estimate requiring explicit boundary coverage."""
    from decimal import Decimal as D

    if end <= start or len(samples) < 2 or samples[0][0] != start or samples[-1][0] != end:
        return None
    weighted = D(0)
    for (t0, value), (t1, _) in itertools.pairwise(samples):
        gap = (t1 - t0).total_seconds()
        if not 0 < gap <= max_gap_seconds:
            return None
        weighted += value * D(str(gap))
    return weighted / D(str((end - start).total_seconds()))


def walk_forward(labels: list[dict], folds: int = 3, embargo_seconds: int = 900) -> list[dict]:
    """Expanding chronological calibration with disjoint market labels and purged training intervals.

    Training estimates a Beta(1,1)-smoothed UP frequency. Only outcomes observable before
    the embargo boundary are used. The untouched last fold is also the final holdout.
    """
    from datetime import timedelta

    if not 2 <= folds <= 10 or embargo_seconds < 0:
        raise ValueError("Invalid walk-forward policy")
    if len(labels) < 2 * (folds + 1):
        return []
    ordered = sorted(labels, key=lambda r: (r["decision_time"], r["market_id"]))
    times = sorted({r["decision_time"] for r in ordered})
    if len(times) < folds + 1:
        return []
    output = []
    for i in range(1, folds + 1):
        start = times[i * len(times) // (folds + 1)]
        end = times[(i + 1) * len(times) // (folds + 1)] if i < folds else None
        training = [r for r in ordered if r["outcome_time"] < start - timedelta(seconds=embargo_seconds)]
        test = [r for r in ordered if start <= r["decision_time"] and (end is None or r["decision_time"] < end)]
        if not training or not test:
            continue
        historical_p = (1 + sum(int(r["y"]) for r in training)) / (2 + len(training))
        output.append(
            {
                "fold": i,
                "final_holdout": i == folds,
                "test_start": start.isoformat(),
                "train_count": len(training),
                "test_count": len(test),
                "embargo_seconds": embargo_seconds,
                "market_probability": calibration([(float(r["p"]), int(r["y"])) for r in test]),
                "historical_frequency": calibration([(historical_p, int(r["y"])) for r in test]),
            }
        )
    return output


def reference_correlation(rows: list[dict]) -> dict:
    """Correlate matched BTC/ETH log returns; keep gaps and unequal timestamps out."""
    from itertools import pairwise
    from statistics import StatisticsError, correlation

    prices: dict = defaultdict(dict)
    for row in rows:
        prices[row["received_at"]][row["asset"]] = float(row["price"])
    paired = sorted((ts, p) for ts, p in prices.items() if "BTC" in p and "ETH" in p)
    changes = [
        (log(b["BTC"] / a["BTC"]), log(b["ETH"] / a["ETH"]))
        for (t, a), (u, b) in pairwise(paired)
        if 0 < (u - t).total_seconds() <= 30 and min(a.values()) > 0 and min(b.values()) > 0
    ]
    r = None
    if len(changes) >= 10:
        try:
            r = correlation([x for x, _ in changes], [y for _, y in changes])
        except StatisticsError:
            pass
    return {
        "matched_returns": len(changes),
        "correlation": r,
        "source": "kraken",
        "quantity": "paired log returns",
    }
