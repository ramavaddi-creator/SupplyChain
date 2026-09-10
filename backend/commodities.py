"""
Master lists for seed generation. Prices are illustrative synthetic
baselines (approx July 2026 India ballpark), NOT live data -- once real
connectors are wired to AGMARKNET/Consumer Affairs/CPI/WPI, these seed
values are replaced by actual ingested observations in the same schema.
"""

CITIES = [
    # city, state, tier, fresh_produce_factor, packaged_goods_factor
    ("Hyderabad", "Telangana", "metro", 1.00, 0.97),
    ("Mumbai", "Maharashtra", "metro", 1.12, 1.05),
    ("Delhi NCR", "Delhi", "metro", 1.05, 1.02),
    ("Bengaluru", "Karnataka", "metro", 1.08, 1.03),
    ("Chennai", "Tamil Nadu", "metro", 1.04, 1.00),
    ("Pune", "Maharashtra", "metro", 1.06, 1.01),
    ("Siddipet", "Telangana", "telangana_regional", 0.88, 1.02),
    ("Nizamabad", "Telangana", "telangana_regional", 0.85, 1.04),
]

# category, commodity, sub_category, standard_unit, essential, structural,
# regional_relevance, has_mandi_chain (agri vs FMCG), base_price (INR per
# standard_unit at Hyderabad), core (full daily x all-city coverage vs
# partial/weekly coverage), basket_weight (essential, actual), freq/month
COMMODITIES = [
    # ---- CORE: full daily coverage across all 8 cities ----
    ("Grains & Staples", "Rice (regular)", None, "kg", 1, 1, "national", True, True, 22, 8.0, 8.0, 20),
    ("Grains & Staples", "Atta", None, "kg", 1, 1, "national", True, True, 24, 7.5, 7.5, 15),
    ("Pulses", "Toor Dal", None, "kg", 1, 1, "national", True, True, 95, 5.0, 5.0, 6),
    ("Pulses", "Moong Dal", None, "kg", 1, 1, "national", True, True, 105, 2.0, 2.0, 3),
    ("Dairy", "Milk", None, "litre", 1, 1, "national", True, True, 42, 9.0, 9.0, 26),
    ("Dairy", "Curd", None, "kg", 1, 0, "national", True, True, 55, 2.0, 2.5, 12),
    ("Dairy", "Ghee", None, "kg", 1, 1, "national", False, True, 520, 3.0, 3.5, 2),
    ("Cooking Oils", "Sunflower Oil", None, "litre", 1, 1, "national", False, True, 118, 6.0, 6.0, 3),
    ("Cooking Oils", "Groundnut Oil", None, "litre", 1, 1, "telangana", False, True, 165, 2.0, 2.5, 2),
    ("Staples", "Sugar", None, "kg", 1, 1, "national", True, True, 40, 2.5, 2.5, 4),
    ("Staples", "Salt", None, "kg", 1, 1, "national", True, True, 20, 0.5, 0.5, 2),
    ("Tea/Coffee", "Tea (mid-market)", None, "kg", 1, 1, "national", False, True, 280, 2.0, 2.0, 2),
    ("Tea/Coffee", "Filter Coffee", None, "kg", 0, 1, "national", False, True, 650, 0.5, 1.0, 1),
    ("Spices", "Jeera", None, "kg", 0, 0, "national", True, True, 380, 0.4, 0.6, 1),
    ("Spices", "Turmeric", None, "kg", 1, 0, "national", True, True, 145, 0.6, 0.8, 1),
    ("Spices", "Red Chilli", None, "kg", 1, 0, "national", True, True, 210, 0.8, 1.0, 1),
    ("Nuts", "Peanuts / Groundnuts", None, "kg", 0, 0, "telangana", True, True, 95, 0.5, 1.0, 2),
    ("Vegetables", "Tomato", None, "kg", 1, 0, "national", True, True, 22, 1.5, 2.5, 12),
    ("Vegetables", "Onion", None, "kg", 1, 0, "national", True, True, 26, 1.5, 2.5, 10),
    ("Vegetables", "Potato", None, "kg", 1, 0, "national", True, True, 18, 1.5, 2.0, 10),

    # ---- NON-CORE: partial coverage, weekly cadence, fewer cities ----
    ("Grains & Staples", "Basmati Rice", None, "kg", 0, 1, "national", True, True, 85, 0, 1.5, 2),
    ("Grains & Staples", "Maida", None, "kg", 0, 1, "national", True, True, 30, 0, 1.0, 2),
    ("Grains & Staples", "Rava / Suji", None, "kg", 0, 1, "national", True, True, 34, 0, 0.8, 2),
    ("Pulses", "Urad Dal", None, "kg", 0, 1, "national", True, True, 110, 0, 1.0, 2),
    ("Pulses", "Chana Dal", None, "kg", 0, 1, "national", True, True, 78, 0, 1.0, 2),
    ("Pulses", "Masoor Dal", None, "kg", 0, 1, "national", True, True, 88, 0, 0.8, 2),
    ("Millets", "Jowar", None, "kg", 0, 0, "telangana", True, False, 32, 0, 1.0, 2),
    ("Millets", "Bajra", None, "kg", 0, 0, "telangana", True, False, 30, 0, 0.6, 1),
    ("Millets", "Ragi", None, "kg", 0, 0, "telangana", True, False, 45, 0, 0.8, 1),
    ("Dairy", "Butter", None, "kg", 0, 1, "national", False, True, 480, 0, 1.0, 1),
    ("Dairy", "Paneer", None, "kg", 0, 0, "national", False, False, 320, 0, 1.5, 3),
    ("Cooking Oils", "Mustard Oil", None, "litre", 0, 1, "national", False, True, 145, 0, 1.0, 1),
    ("Cooking Oils", "Rice Bran Oil", None, "litre", 0, 1, "national", False, True, 155, 0, 0.8, 1),
    ("Nuts", "Cashew", None, "kg", 0, 0, "national", False, False, 780, 0, 0.5, 1),
    ("Spices", "Elaichi / Cardamom", None, "kg", 0, 0, "national", False, False, 2400, 0, 0.3, 1),
    ("Spices", "Black Pepper", None, "kg", 0, 0, "national", False, False, 620, 0, 0.3, 1),
    ("Vegetables", "Green Chilli", None, "kg", 1, 0, "national", True, False, 35, 0, 1.0, 8),
    ("Vegetables", "Lemon", None, "kg", 0, 0, "national", True, False, 45, 0, 0.6, 6),
    ("Vegetables", "Ginger", None, "kg", 0, 0, "national", True, False, 65, 0, 0.6, 4),
    ("Vegetables", "Garlic", None, "kg", 0, 0, "national", True, False, 85, 0, 0.6, 4),
    ("Vegetables", "Coriander", None, "kg", 0, 0, "national", True, False, 28, 0, 0.5, 8),
    ("Vegetables", "Brinjal", None, "kg", 0, 0, "national", True, False, 24, 0, 0.8, 6),
    ("Vegetables", "Okra", None, "kg", 0, 0, "national", True, False, 30, 0, 0.8, 6),
    ("Vegetables", "Cauliflower", None, "kg", 0, 0, "national", True, False, 26, 0, 0.6, 5),
    ("Vegetables", "Cabbage", None, "kg", 0, 0, "national", True, False, 20, 0, 0.6, 5),
    ("Vegetables", "Carrot", None, "kg", 0, 0, "national", True, False, 32, 0, 0.6, 5),
    ("Household Cleaning", "Washing Powder", None, "kg", 1, 1, "national", False, True, 145, 1.5, 1.5, 3),
    ("Household Cleaning", "Dishwash", None, "kg", 0, 1, "national", False, True, 110, 0, 0.6, 2),
    ("Household Cleaning", "Floor Cleaner", None, "litre", 0, 1, "national", False, True, 95, 0, 0.5, 1),
    ("Personal Care", "Bath Soap", None, "kg", 1, 1, "national", False, True, 210, 1.0, 1.0, 3),
    ("Personal Care", "Toothpaste", None, "kg", 1, 1, "national", False, True, 380, 1.0, 1.0, 2),
    ("Personal Care", "Shampoo", None, "litre", 0, 1, "national", False, True, 420, 0, 0.8, 1),
]

# channel key, label, lag_days_from_upstream, alpha_up, alpha_down, base_ratio
# alpha_up/down = how much of the upstream % move passes through (asymmetric
# = stickiness). base_ratio = multiplier applied to mandi/base price chain.
CHANNEL_CHAIN = [
    ("mandi", "Mandi", 0, 1.0, 1.0, 1.00),
    ("official", "Official Benchmark", 2, 0.85, 0.55, 1.15),
    ("local_retail", "Local Retail", 6, 0.80, 0.40, 1.45),
    ("supermarket", "Supermarket", 11, 0.75, 0.35, 1.62),
]
# quick_commerce is derived as a premium over supermarket, not over mandi
QC_PREMIUM_RANGE = (0.06, 0.16)

# For FMCG (has_mandi_chain=False): only local_retail/supermarket/quick_commerce
FMCG_CHAIN = [
    ("local_retail", "Local Retail", 0, 1.0, 1.0, 1.00),
    ("supermarket", "Supermarket", 3, 0.9, 0.6, 1.10),
]

# Illustrative seasonal multipliers (India-typical patterns), month 1-12.
# Applied on top of the random-walk trend so the seed data has a genuine,
# detectable recurring pattern -- not just noise. Commodities not listed
# default to a flat 1.0 (no seasonal assumption imposed).
SEASONAL_FACTORS = {
    "Tomato":        {1:0.85,2:0.82,3:0.88,4:0.95,5:1.00,6:1.05,7:1.18,8:1.25,9:1.15,10:1.00,11:0.90,12:0.85},
    "Onion":         {1:0.85,2:0.85,3:0.85,4:0.95,5:1.00,6:1.05,7:1.05,8:1.10,9:1.20,10:1.15,11:1.05,12:0.90},
    "Potato":        {1:0.85,2:0.82,3:0.85,4:0.95,5:1.00,6:1.05,7:1.10,8:1.15,9:1.15,10:1.10,11:1.00,12:0.90},
    "Green Chilli":  {1:0.90,2:0.88,3:0.90,4:0.95,5:1.00,6:1.08,7:1.15,8:1.18,9:1.10,10:1.00,11:0.92,12:0.90},
    "Turmeric":      {1:0.85,2:0.82,3:0.85,4:0.95,5:1.05,6:1.10,7:1.10,8:1.08,9:1.05,10:1.00,11:0.95,12:0.90},
    "Red Chilli":    {1:0.90,2:0.85,3:0.85,4:0.92,5:1.02,6:1.08,7:1.10,8:1.08,9:1.05,10:1.00,11:0.95,12:0.92},
    "Jeera":         {1:0.95,2:0.88,3:0.85,4:0.90,5:1.05,6:1.10,7:1.08,8:1.05,9:1.02,10:1.00,11:0.98,12:0.98},
    "Peanuts / Groundnuts": {1:1.00,2:1.02,3:1.02,4:1.00,5:1.00,6:1.05,7:1.08,8:1.05,9:0.95,10:0.85,11:0.85,12:0.92},
    "Milk":          {1:0.96,2:0.97,3:1.00,4:1.03,5:1.05,6:1.04,7:1.00,8:1.00,9:1.00,10:1.00,11:0.97,12:0.96},
    "Ghee":          {1:0.98,2:0.98,3:1.00,4:1.00,5:1.00,6:1.00,7:1.00,8:1.02,9:1.03,10:1.06,11:1.05,12:1.00},
    "Sugar":         {1:0.98,2:0.98,3:1.00,4:1.00,5:1.00,6:1.00,7:1.00,8:1.02,9:1.04,10:1.06,11:1.04,12:1.00},
    "Sunflower Oil": {1:1.00,2:1.00,3:1.00,4:1.00,5:1.00,6:1.00,7:1.00,8:1.02,9:1.03,10:1.05,11:1.03,12:1.00},
}


def seasonal_multiplier(commodity, month):
    return SEASONAL_FACTORS.get(commodity, {}).get(month, 1.0)


# --- Regional supply/demand layer (V1 scope) ---
# Population figures are ROUGH illustrative approximations (2026 ballpark,
# metro area), not an authoritative census figure -- used only to scale a
# synthetic consumption index, never displayed as a demographic claim.
CITY_POPULATION_MILLIONS = {
    "Hyderabad": 10.5, "Mumbai": 21.5, "Delhi NCR": 32.0, "Bengaluru": 13.5,
    "Chennai": 11.5, "Pune": 7.5, "Siddipet": 0.11, "Nizamabad": 0.35,
}

# Per-capita monthly consumption (kg or litres/person/month), illustrative
# India-typical ballpark by category -- used to derive a CONSUMPTION INDEX,
# not presented as an NSSO-sourced figure until real survey data replaces it.
PER_CAPITA_MONTHLY_CONSUMPTION = {
    "Rice (regular)": 5.5, "Atta": 4.8, "Toor Dal": 0.9, "Moong Dal": 0.4,
    "Milk": 4.5, "Sunflower Oil": 0.7, "Groundnut Oil": 0.5, "Sugar": 0.9,
    "Salt": 0.3, "Tomato": 2.2, "Onion": 2.5, "Potato": 2.0,
    "Turmeric": 0.12, "Red Chilli": 0.15, "Jeera": 0.06,
    "Peanuts / Groundnuts": 0.3,
}

# Typical daily mandi arrivals (tonnes) at a representative production-linked
# mandi, illustrative baseline -- real AGMARKNET data reports this per
# market/day; this is the seed-data placeholder until connectors are live.
TYPICAL_DAILY_ARRIVALS_TONNES = {
    "Rice (regular)": 450, "Atta": 0, "Toor Dal": 180, "Moong Dal": 90,
    "Sugar": 220, "Tomato": 320, "Onion": 380, "Potato": 300,
    "Turmeric": 60, "Red Chilli": 45, "Jeera": 25,
    "Peanuts / Groundnuts": 70, "Basmati Rice": 90,
}
