"""Independent stdlib-only synthetic oracles; never production publication policy."""

from calendar import monthrange
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext


def continuous(values, probability):
    """Interpolate a sorted sample by hand, without SQL/numpy/app helpers."""
    sample = sorted(Decimal(str(value)) for value in values)
    if any(not value.is_finite() or value <= 0 for value in sample):
        raise ValueError("Synthetic price sample must contain finite positive values")
    if not sample:
        return None
    position = (len(sample) - 1) * Decimal(str(probability))
    left = int(position)
    fraction = position - left
    return sample[left] + fraction * (
        sample[min(left + 1, len(sample) - 1)] - sample[left]
    )


def price_summary(values, *, synthetic_median_n):
    quantiles = {
        name: continuous(values, p)
        for name, p in (("p10", "0.1"), ("p50", "0.5"), ("p90", "0.9"))
    }
    return (
        {"n": len(values)}
        | quantiles
        | {
            "median_public": quantiles["p50"]
            if len(values) >= synthetic_median_n
            else None
        }
    )


def assert_price_case(case, *, synthetic_median_n):
    actual = price_summary(case["values"], synthetic_median_n=synthetic_median_n)
    expected = {
        key: Decimal(value) if isinstance(value, str) else value
        for key, value in case["expected"].items()
    }
    assert actual == expected


def ordinal_summary(classes):
    if any(value is not None and value not in tuple("ABCDEFG") for value in classes):
        raise ValueError("Unknown energy label must be represented explicitly")
    sample = sorted(value for value in classes if value is not None)
    frequencies = Counter(sample)
    maximum = max(frequencies.values(), default=0)
    return {
        "total": len(classes),
        "known": len(sample),
        "unknown": classes.count(None),
        "modes": sorted(value for value, n in frequencies.items() if n == maximum),
        "median": sample[(len(sample) - 1) // 2] if sample else None,
        "fg_numerator": frequencies["F"] + frequencies["G"],
    }


def assert_energy_case(case):
    assert ordinal_summary(case["classes"]) == case["expected"]


def window_members(anchor, dates):
    end = date.fromisoformat(anchor)

    def earlier(years):
        year = end.year - years
        return date(year, end.month, min(end.day, monthrange(year, end.month)[1]))

    middle, start = earlier(1), earlier(2)
    parsed = [date.fromisoformat(value) for value in dates]
    return (
        [value.isoformat() for value in parsed if start <= value < middle],
        [value.isoformat() for value in parsed if middle <= value < end],
    )


def assert_window_case(case):
    assert window_members(case["anchor"], case["dates"]) == (
        case["expected_previous"],
        case["expected_current"],
    )


def variation(previous, current, *, complete_source):
    if not complete_source or previous is None or current is None:
        return None
    with localcontext() as context:
        context.prec = 28
        a, b = Decimal(previous), Decimal(current)
        if not a.is_finite() or not b.is_finite() or a <= 0 or b <= 0:
            raise ValueError("Variation requires positive finite medians")
        return (b / a - 1) * 100


def canonical_documents(observations):
    documents = {}
    for row in observations:
        identity = row["id"]
        if identity in documents and documents[identity] != row["class"]:
            raise ValueError("Conflicting observations cannot silently choose a class")
        documents[identity] = row["class"]
    return documents
