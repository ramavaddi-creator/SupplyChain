"""
Single source of truth for the two external drivers (rainfall, diesel),
used by BOTH drivers_seed.py (populates external_drivers) and seed.py
(modulates actual commodity prices). Having one shared module -- rather
than two independent random generators -- is what makes the driver
correlation engine have a genuine relationship to detect, instead of two
coincidentally-seasonal but causally-unrelated series.
"""
import random
from datetime import date, timedelta

TODAY = date(2026, 7, 20)
DAYS = 730
START_DATE = TODAY - timedelta(days=DAYS - 1)

_rng = random.Random(99)  # separate, fixed stream so it doesn't perturb seed.py's own random sequence

RAINFALL_NORMAL_BY_MONTH = {1: 8, 2: 10, 3: 15, 4: 25, 5: 45, 6: 140, 7: 210, 8: 190, 9: 150, 10: 60, 11: 15, 12: 6}


def _months_25():
    months = []
    y, m = TODAY.year, TODAY.month
    for _ in range(25):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()
    return months


_MONTHLY_RAINFALL = {}
for (yy, mm) in _months_25():
    normal = RAINFALL_NORMAL_BY_MONTH[mm]
    _MONTHLY_RAINFALL[(yy, mm)] = max(0, normal * _rng.uniform(0.65, 1.4))

_DAILY_DIESEL = {}
_price = 91.5
for i in range(DAYS):
    d = START_DATE + timedelta(days=i)
    if _rng.random() < 0.03:
        _price += _rng.uniform(-1.8, 2.2)
    _price += _rng.gauss(0, 0.03)
    _price = max(_price, 60)
    _DAILY_DIESEL[d.isoformat()] = round(_price, 2)

# --- international trade drivers ---
# Global edible oil benchmark (blended palm/soy/sunflower, USD/tonne) -- India
# imports a large share of its edible oil requirement, so this is a
# defensible real-world import-cost driver, not a stretch.
_GLOBAL_OIL_USD = {}
_oil = 980.0
for i in range(DAYS):
    d = START_DATE + timedelta(days=i)
    if _rng.random() < 0.015:  # geopolitical/supply shock, less frequent than diesel revisions
        _oil += _rng.uniform(-90, 110)
    _oil += _rng.gauss(0, 3.5)
    _oil = max(_oil, 500)
    _GLOBAL_OIL_USD[d.isoformat()] = round(_oil, 1)

# INR/USD rate -- slow depreciation drift + noise, real-world-typical direction
_INR_USD = {}
_fx = 83.4
for i in range(DAYS):
    d = START_DATE + timedelta(days=i)
    _fx += _rng.gauss(0.0015, 0.03)  # slight depreciation drift
    _fx = max(_fx, 70)
    _INR_USD[d.isoformat()] = round(_fx, 3)

# Global export-reference prices (USD/tonne) for two commodities India
# exports meaningfully (onion, rice) -- world price pressure that can pull
# domestic supply toward export markets, with domestic policy typically
# damping the effect (export curbs, MEP changes, etc. -- not modeled here,
# kept as a simple exposure factor in seed.py).
_GLOBAL_EXPORT = {"onion": {}, "rice": {}}
_export_base = {"onion": 340.0, "rice": 410.0}
for key, base in _export_base.items():
    val = base
    for i in range(DAYS):
        d = START_DATE + timedelta(days=i)
        if _rng.random() < 0.02:
            val += _rng.uniform(-25, 30)
        val += _rng.gauss(0, 2.2)
        val = max(val, 150)
        _GLOBAL_EXPORT[key][d.isoformat()] = round(val, 1)


def rainfall_actual(year, month):
    return round(_MONTHLY_RAINFALL.get((year, month), RAINFALL_NORMAL_BY_MONTH.get(month, 50)), 1)


def rainfall_deviation_ratio(year, month):
    """actual / seasonal-normal for that month. <1 = drought-ish, >1 = surplus."""
    actual = _MONTHLY_RAINFALL.get((year, month))
    normal = RAINFALL_NORMAL_BY_MONTH.get(month, 50)
    if actual is None or normal == 0:
        return 1.0
    return actual / normal


def diesel_price(iso_date):
    return _DAILY_DIESEL.get(iso_date)


def import_cost_index(iso_date):
    """Global edible oil price x FX rate -- the actual real-world mechanic
    for what an import costs in rupees. Returned as an index number, not a
    real per-litre price, so it's never mistaken for an actual retail figure."""
    oil = _GLOBAL_OIL_USD.get(iso_date)
    fx = _INR_USD.get(iso_date)
    if oil is None or fx is None:
        return None
    return round(oil * fx / 1000, 2)


def global_export_price(commodity_key, iso_date):
    return _GLOBAL_EXPORT.get(commodity_key, {}).get(iso_date)


def all_months():
    return _months_25()
