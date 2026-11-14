"""Business reference data for the simulated catering + café kitchen.

Everything here is fictional but sized to feel like a real small food business
in the GTA: a catering line serving school-board staff events (weekday heavy,
quiet in summer, busy in December) plus a small in-house café.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

SIM_START = date(2023, 1, 2)
SIM_END = date(2025, 8, 31)  # previous owner's data ends at handover
DEFAULT_SEED = 42

LB_TO_KG = 0.45359237


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    aliases: tuple[str, ...]          # how the name shows up on messy exports
    order_weekdays: tuple[int, ...]   # 0=Mon
    lead_days: float                  # mean lead time
    lead_sd: float
    on_time_p: float                  # probability a delivery lands on/before promise
    fill_p: float                     # probability a line ships complete
    markup: float                     # price vs market benchmark
    price_noise: float                # week-to-week price wobble
    price_as_text: bool = False       # this vendor's system exports "$1,234.50"


VENDORS: dict[str, Vendor] = {
    v.vendor_id: v
    for v in [
        Vendor("V01", "Northfield Foodservice",
               ("Northfield Foodservice", "NORTHFIELD FOODSERVICE INC", "Northfield Food Service", "northfield"),
               (1, 4), 2, 0.6, 0.93, 0.95, 1.08, 0.02),
        Vendor("V02", "Peel Fresh Produce",
               ("Peel Fresh Produce", "PEEL FRESH", "Peel Fresh Produce Ltd.", "Peel Fresh  Produce"),
               (0, 3), 1, 0.5, 0.86, 0.90, 1.12, 0.05),
        Vendor("V03", "Lakeshore Meats",
               ("Lakeshore Meats", "LAKESHORE MEATS & PROVISIONS", "Lakeshore Meat", "lakeshore meats"),
               (0, 2, 4), 2, 0.8, 0.88, 0.92, 1.10, 0.06),
        Vendor("V04", "Credit Valley Dairy",
               ("Credit Valley Dairy", "CREDIT VALLEY DAIRY CO", "Credit Vly Dairy"),
               (1, 4), 1, 0.3, 0.96, 0.97, 1.05, 0.01),
        Vendor("V05", "Spice Route Imports",
               ("Spice Route Imports", "SPICE ROUTE IMPORTS LTD", "SpiceRoute", "Spice Route"),
               (0,), 7, 2.5, 0.72, 0.85, 1.15, 0.07, price_as_text=True),
        Vendor("V06", "GTA Packaging Supply",
               ("GTA Packaging Supply", "G.T.A. PACKAGING", "GTA Packaging"),
               (2,), 3, 1.0, 0.90, 0.94, 1.06, 0.02),
        Vendor("V07", "Halton Bakery Co",
               ("Halton Bakery Co", "HALTON BAKERY", "Halton Bakery Company"),
               (0, 1, 2, 3, 4), 1, 0.1, 0.97, 0.98, 1.04, 0.01),
        Vendor("V08", "Bargain Cash & Carry",
               ("Bargain Cash & Carry", "BARGAIN CASH AND CARRY", "Bargain C&C", "bargain cash&carry"),
               (), 0, 0.0, 1.0, 1.0, 1.28, 0.08),  # emergency walk-in backup
    ]
}
BACKUP_VENDOR = "V08"


@dataclass(frozen=True)
class Item:
    item_id: str
    name: str
    category: str
    base_uom: str            # kg | L | ea
    base_price: float        # CAD per base unit, Jan 2023 market
    daily_use: float         # base units on a normal weekday
    shelf_life: int          # days
    vol: float               # monthly market price volatility
    seasonal_amp: float      # produce-style seasonal swing
    vendor_id: str
    vendor_uom: str          # how the vendor invoices it
    pack_size: float         # base units per vendor uom
    count_names: tuple[str, ...] = field(default=())   # sloppy names on count sheets


ITEMS: list[Item] = [
    # --- Produce (Peel Fresh) ---
    Item("P01", "Romaine Lettuce", "Produce", "kg", 4.20, 4.0, 7, 0.08, 0.25, "V02", "CS", 10.0, ("romaine", "Romaine lettuce", "lettuce - romaine")),
    Item("P02", "Roma Tomatoes", "Produce", "kg", 3.80, 5.5, 7, 0.08, 0.30, "V02", "CS", 11.34, ("roma tomato", "Tomatoes Roma", "tomatoes")),
    Item("P03", "Yellow Onions", "Produce", "kg", 1.60, 7.0, 30, 0.05, 0.10, "V02", "BAG", 22.68, ("onions", "Yellow onion", "onion yellow")),
    Item("P04", "Russet Potatoes", "Produce", "kg", 1.30, 9.0, 30, 0.04, 0.10, "V02", "BAG", 22.68, ("potatoes", "Russet potato")),
    Item("P05", "Carrots", "Produce", "kg", 1.50, 4.0, 21, 0.04, 0.10, "V02", "BAG", 22.68, ("carrot", "Carrots ")),
    Item("P06", "Bell Peppers (Mixed)", "Produce", "kg", 5.50, 2.8, 10, 0.10, 0.35, "V02", "CS", 5.0, ("peppers", "bell pepper", "Peppers mixed")),
    Item("P07", "English Cucumbers", "Produce", "kg", 3.00, 2.5, 10, 0.08, 0.30, "V02", "CS", 8.0, ("cucumber", "cukes")),
    Item("P08", "Peeled Garlic", "Produce", "kg", 9.50, 0.5, 21, 0.06, 0.05, "V02", "CS", 2.27, ("garlic", "garlic peeled")),
    Item("P09", "Lemons", "Produce", "kg", 4.20, 1.0, 21, 0.07, 0.15, "V02", "CS", 8.0, ("lemon",)),
    Item("P10", "Cilantro", "Produce", "ea", 1.20, 6.0, 5, 0.10, 0.20, "V02", "CS", 30, ("cilantro bunch", "coriander")),
    Item("P11", "Gala Apples", "Produce", "kg", 3.40, 3.0, 30, 0.05, 0.20, "V02", "CS", 18.0, ("apples", "apple gala")),
    Item("P12", "Bananas", "Produce", "kg", 1.90, 4.0, 5, 0.05, 0.05, "V02", "CS", 18.0, ("banana",)),
    # --- Protein (Lakeshore, invoiced per lb) ---
    Item("M01", "Chicken Breast (Boneless)", "Protein", "kg", 13.50, 11.0, 4, 0.06, 0.05, "V03", "LB", 0.4536, ("chicken breast", "Chkn breast", "chicken brst")),
    Item("M02", "Chicken Thigh (Boneless)", "Protein", "kg", 10.20, 9.0, 4, 0.06, 0.05, "V03", "LB", 0.4536, ("chicken thigh", "chkn thigh")),
    Item("M03", "Lean Ground Beef", "Protein", "kg", 12.80, 5.0, 3, 0.07, 0.03, "V03", "LB", 0.4536, ("ground beef", "beef ground", "lean gr beef")),
    Item("M04", "Beef Striploin", "Protein", "kg", 32.00, 1.2, 5, 0.09, 0.08, "V03", "LB", 0.4536, ("striploin", "beef strip")),
    Item("M05", "Pork Shoulder", "Protein", "kg", 7.90, 2.5, 5, 0.06, 0.05, "V03", "LB", 0.4536, ("pork shoulder", "pork butt")),
    Item("M06", "Atlantic Salmon Fillet", "Protein", "kg", 26.00, 1.8, 3, 0.10, 0.06, "V03", "LB", 0.4536, ("salmon", "salmon fillet")),
    # --- Dairy & eggs (Credit Valley) ---
    Item("D01", "Milk 2%", "Dairy", "L", 1.85, 14.0, 10, 0.02, 0.00, "V04", "JUG", 4.0, ("milk", "2% milk", "milk 2 %")),
    Item("D02", "Unsalted Butter", "Dairy", "kg", 11.00, 1.4, 60, 0.05, 0.00, "V04", "CS", 7.26, ("butter",)),
    Item("D03", "Old Cheddar", "Dairy", "kg", 14.50, 2.0, 45, 0.04, 0.00, "V04", "CS", 9.07, ("cheddar", "cheese cheddar")),
    Item("D04", "Shredded Mozzarella", "Dairy", "kg", 11.80, 2.6, 30, 0.04, 0.00, "V04", "CS", 9.07, ("mozza", "mozzarella shred")),
    Item("D05", "Whipping Cream 35%", "Dairy", "L", 7.90, 1.6, 14, 0.03, 0.00, "V04", "CS", 12.0, ("cream", "35% cream", "whipping cream")),
    Item("D06", "Plain Yogurt", "Dairy", "kg", 4.50, 2.0, 21, 0.03, 0.00, "V04", "CS", 6.0, ("yogurt", "yoghurt plain")),
    Item("D07", "Large Eggs", "Dairy", "ea", 0.38, 110, 21, 0.05, 0.00, "V04", "CS", 180, ("eggs", "eggs large", "egg")),
    # --- Dry goods (Northfield broadline) ---
    Item("G01", "Basmati Rice", "Dry Goods", "kg", 3.40, 8.0, 365, 0.03, 0.00, "V01", "BAG", 18.14, ("rice", "basmati")),
    Item("G02", "All-Purpose Flour", "Dry Goods", "kg", 1.30, 4.0, 180, 0.04, 0.00, "V01", "BAG", 20.0, ("flour", "AP flour")),
    Item("G03", "Canola Oil", "Dry Goods", "L", 3.10, 4.0, 270, 0.09, 0.00, "V01", "JUG", 16.0, ("oil", "canola", "fryer oil")),
    Item("G04", "Penne Pasta", "Dry Goods", "kg", 2.60, 4.0, 365, 0.03, 0.00, "V01", "CS", 9.07, ("penne", "pasta penne")),
    Item("G05", "Granulated Sugar", "Dry Goods", "kg", 1.60, 2.0, 365, 0.04, 0.00, "V01", "BAG", 20.0, ("sugar",)),
    Item("G06", "Diced Tomatoes (Canned)", "Dry Goods", "kg", 2.10, 5.0, 540, 0.03, 0.00, "V01", "CS", 17.0, ("canned tomato", "diced tomatoes", "tom diced can")),
    Item("G07", "Coffee Beans (Espresso)", "Dry Goods", "kg", 24.00, 1.2, 120, 0.08, 0.00, "V01", "BAG", 2.27, ("coffee", "espresso beans")),
    # --- Spices & legumes (Spice Route, long lead time) ---
    Item("S01", "Garam Masala", "Spices", "kg", 28.00, 0.10, 365, 0.08, 0.00, "V05", "BAG", 1.0, ("garam masala",)),
    Item("S02", "Ground Turmeric", "Spices", "kg", 16.00, 0.08, 365, 0.08, 0.00, "V05", "BAG", 1.0, ("turmeric",)),
    Item("S03", "Cumin Seed", "Spices", "kg", 18.00, 0.08, 365, 0.09, 0.00, "V05", "BAG", 1.0, ("cumin", "jeera")),
    Item("S04", "Chili Powder", "Spices", "kg", 15.00, 0.10, 365, 0.08, 0.00, "V05", "BAG", 1.0, ("chili powder", "chilli pwd")),
    Item("S05", "Red Lentils", "Spices", "kg", 3.60, 2.0, 365, 0.05, 0.00, "V05", "BAG", 20.0, ("lentils", "masoor dal", "red lentil")),
    Item("S06", "Dried Chickpeas", "Spices", "kg", 3.20, 1.5, 365, 0.05, 0.00, "V05", "BAG", 20.0, ("chickpeas", "chana")),
    # --- Bakery (Halton, daily delivery) ---
    Item("B01", "Sandwich Loaf", "Bakery", "ea", 3.20, 16, 5, 0.03, 0.00, "V07", "EA", 1, ("bread", "loaf", "sandwich bread")),
    Item("B02", "Dinner Rolls", "Bakery", "ea", 0.45, 80, 4, 0.03, 0.00, "V07", "DZ", 12, ("rolls", "dinner roll")),
    Item("B03", "Butter Croissants", "Bakery", "ea", 1.10, 28, 3, 0.03, 0.00, "V07", "DZ", 12, ("croissant", "croissants")),
    # --- Packaging (GTA Packaging) ---
    Item("K01", "Full-Size Foil Pans", "Packaging", "ea", 1.05, 28, 9999, 0.04, 0.00, "V06", "CS", 50, ("foil pans", "full pans", "pans foil")),
    Item("K02", "Compostable Clamshells", "Packaging", "ea", 0.42, 100, 9999, 0.04, 0.00, "V06", "CS", 200, ("clamshells", "takeout boxes")),
    Item("K03", "12oz Hot Cups", "Packaging", "ea", 0.14, 85, 9999, 0.04, 0.00, "V06", "CS", 1000, ("hot cups", "cups 12oz")),
    Item("K04", "Nitrile Gloves (Box)", "Packaging", "ea", 9.50, 1.0, 9999, 0.06, 0.00, "V06", "CS", 10, ("gloves", "nitrile gloves")),
]
ITEM_BY_ID = {i.item_id: i for i in ITEMS}

# Annual food inflation profile (monthly compounding) by category, loosely shaped like
# the post-2022 cool-down: hot 2023, calmer 2024, a beef-led protein bump in 2025.
CATEGORY_INFLATION = {
    # category: {year: annual rate}
    "Produce":   {2023: 0.075, 2024: 0.030, 2025: 0.035},
    "Protein":   {2023: 0.060, 2024: 0.035, 2025: 0.080},
    "Dairy":     {2023: 0.045, 2024: 0.025, 2025: 0.020},
    "Dry Goods": {2023: 0.070, 2024: 0.020, 2025: 0.025},
    "Spices":    {2023: 0.050, 2024: 0.040, 2025: 0.045},
    "Bakery":    {2023: 0.080, 2024: 0.030, 2025: 0.025},
    "Packaging": {2023: 0.040, 2024: 0.020, 2025: 0.030},
}
BEEF_EXTRA_2025 = 0.10  # extra annual drift on beef items in 2025
