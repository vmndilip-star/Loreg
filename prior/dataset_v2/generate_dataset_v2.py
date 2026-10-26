#!/usr/bin/env python3
"""Loreg dataset generator v2 (deterministic).

Every product gets a market price derived from a market ANCHOR TABLE (cheapest price by category/size/segment,
each anchor tagged with price type, date, evidence level and source). Cost is derived from the market price at
the month the stock was bought, so "stock bought high, market since dropped" emerges naturally.

Usage:  python generate_dataset_v2.py --size demo|full --as-of 2026-10-26 --seed 2026 --out out

Honest limits: brand/model names are real product lines; prices, costs, quantities and sales are SYNTHETIC and
anchored to the market_price_reference.csv table. Only anchors tagged sourced_2026 / sourced_older have a source;
'estimated' anchors are my assumptions. Refresh the anchors before making any pricing claim.
"""
import argparse, csv, hashlib, math, random
from datetime import date, timedelta
from pathlib import Path
import numpy as np

from loreg_rules import TH, DEPT, classify, SEASONAL_MULT

# ============================================================================== market anchors
T2 = "https://www.techradar.com/seasonal-sales/shopping-for-a-65-inch-tv-our-experts-have-rounded-up-the-best-prime-day-deals-from-samsung-lg-and-tcl-starting-at-just-usd259-99"
TG = "https://tomsguide.com/deals/tcl-75-inch-4k-tv-deal"
TG24 = "https://www.tomsguide.com/us/best-75-inch-tv-deals,news-28644.html"
MOTO = "https://www.phonearena.com/news/motorola-moto-g-2026-amazon-deal-rare-discount-more-compelling-than-usual_id181723"
MOTO2 = "https://androidmagazine.eu/en/2025/11/05/motorola-predstavila-prvni-modely-rady-moto-g-pro-rok-2026-potesi-vydrzi-i-selfie-kamerou/"
WASH = "https://www.analyticsinsight.net/deals/best-buy-labor-day-deals-2026-that-are-still-around-top-savings-on-tvs-laptops-appliances"
# (anchor_id, category, segment, price_type, usd, observed_on, evidence, source_url, note)
ANCHORS = [
    ("tv_43_promo", "tv", "43", "promo_floor", 69.00, "2025-06", "sourced_older", TG, "Doorbuster: Insignia 43in F30 'from $69' at Best Buy (price check)."),
    ("tv_43_street", "tv", "43", "street_floor", 199.99, "2026-10", "estimated", "", "Assumption: cheapest regular price."),
    ("tv_50_promo", "tv", "50", "promo_floor", 149.00, "2026-10", "estimated", "", "Assumption."),
    ("tv_50_street", "tv", "50", "street_floor", 229.99, "2026-10", "estimated", "", "Assumption."),
    ("tv_55_promo", "tv", "55", "promo_floor", 228.00, "2025-06", "sourced_older", TG, "Onn 55in 4K Roku TV sale $228 at Walmart."),
    ("tv_55_street", "tv", "55", "street_floor", 249.99, "2026-10", "estimated", "", "Assumption."),
    ("tv_65_promo", "tv", "65", "promo_floor", 249.99, "2026-07", "sourced_2026", T2, "Insignia 65in F50 at $249.99 during Prime Day 2026 (sale price, not regular)."),
    ("tv_65_street", "tv", "65", "street_floor", 329.99, "2026-10", "estimated", "", "Assumption: low-quality secondary says most 65in sets sell $330-$600."),
    ("tv_75_promo", "tv", "75", "promo_floor", 429.00, "2025-06", "sourced_older", TG, "Toshiba 75in Fire TV $429 at Best Buy."),
    ("tv_75_street", "tv", "75", "street_floor", 498.00, "2024-05", "sourced_older", TG24, "Onn 75in 4K Roku TV list $498 at Walmart."),
    ("tv_85_promo", "tv", "85", "promo_floor", 599.00, "2026-10", "estimated", "", "Assumption."),
    ("tv_85_street", "tv", "85", "street_floor", 799.99, "2026-10", "estimated", "", "Assumption."),
    ("phone_entry_street", "phone", "entry", "street_floor", 169.99, "2025-11", "sourced_older", MOTO2, "Moto G Play (2026) launch price $169."),
    ("phone_entry_promo", "phone", "entry", "promo_floor", 129.99, "2026-10", "estimated", "", "Assumption."),
    ("phone_budget_street", "phone", "budget", "street_floor", 249.99, "2026-10", "sourced_2026", MOTO, "Moto G (2026): list raised $199.99 -> $299.99; street ~$250 (30-day avg ~$305)."),
    ("phone_budget_promo", "phone", "budget", "promo_floor", 199.99, "2026-10", "sourced_2026", MOTO, "Moto G (2026) all-time low reported $199.99."),
    ("laptop_entry_street", "laptop", "entry", "street_floor", 279.99, "2026-10", "estimated", "", "Assumption: no 2026 floor found."),
    ("laptop_entry_promo", "laptop", "entry", "promo_floor", 199.99, "2026-10", "estimated", "", "Assumption."),
    ("tablet_entry_street", "tablet", "entry", "street_floor", 99.99, "2026-10", "estimated", "", "Assumption."),
    ("tablet_entry_promo", "tablet", "entry", "promo_floor", 59.99, "2026-10", "estimated", "", "Assumption."),
    ("washer_top_street", "washer", "top_load", "street_floor", 599.99, "2026-09", "sourced_2026", WASH, "Insignia 3.7 cu ft top-load $399.99 after $200 off (Labor Day 2026; secondary aggregator)."),
    ("washer_top_promo", "washer", "top_load", "promo_floor", 399.99, "2026-09", "sourced_2026", WASH, "Same source, sale price."),
    ("washer_front_street", "washer", "front_load", "street_floor", 749.99, "2026-10", "estimated", "", "Assumption."),
    ("washer_front_promo", "washer", "front_load", "promo_floor", 599.99, "2026-10", "estimated", "", "Assumption."),
    ("washer_dryer_street", "washer_dryer", "combo", "street_floor", 1199.99, "2026-10", "estimated", "", "Assumption."),
    ("washer_dryer_promo", "washer_dryer", "combo", "promo_floor", 999.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_top_street", "refrigerator", "top_freezer", "street_floor", 699.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_top_promo", "refrigerator", "top_freezer", "promo_floor", 549.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_side_street", "refrigerator", "side_by_side", "street_floor", 1199.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_side_promo", "refrigerator", "side_by_side", "promo_floor", 999.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_french_street", "refrigerator", "french_door", "street_floor", 1799.99, "2026-10", "estimated", "", "Assumption."),
    ("fridge_french_promo", "refrigerator", "french_door", "promo_floor", 1499.99, "2026-10", "estimated", "", "Assumption."),
]
A = {a[0]: dict(zip(("id", "category", "segment", "ptype", "usd", "observed_on", "evidence", "url", "note"), a)) for a in ANCHORS}
TV_SIZES_A = [43, 50, 55, 65, 75, 85]


def apply_anchor_overrides(path):
    """Update existing anchors from a CSV with the same columns as market_price_reference.csv (matched on anchor_id).
    Use it to refresh prices from a live source. Unknown anchor_ids are rejected so typos cannot pass silently."""
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            aid = row["anchor_id"]
            if aid not in A:
                raise SystemExit(f"unknown anchor_id in overrides: {aid}")
            A[aid].update(usd=float(row["usd"]), observed_on=row["observed_on"], evidence=row["evidence"],
                          url=row.get("source_url", ""), note=row.get("note", ""))
    ANCHORS[:] = [tuple(A[a[0]][k] for k in ("id", "category", "segment", "ptype", "usd", "observed_on", "evidence", "url", "note")) for a in ANCHORS]


def tv_floor(size, ptype):
    xs = TV_SIZES_A
    ys = [A[f"tv_{s}_{'promo' if ptype == 'promo_floor' else 'street'}"]["usd"] for s in xs]
    if size <= xs[0]:
        return ys[0] * (size / xs[0]) ** 1.5
    if size >= xs[-1]:
        return ys[-1] * (size / xs[-1]) ** 2.5
    return float(np.interp(size, xs, ys))


def psych(x):
    if x < 10:
        return round(x * 100) / 100
    n = int(round(x))
    return max(n - 0.01, 0.99)


# ============================================================================== catalog
# TVs: (brand, family label, pattern, tier, year, sizes, launch_family)
TV_LEADER = {43: "Insignia F50 Fire TV", 50: "Insignia F50 Fire TV", 65: "Insignia F50 Fire TV", 55: "Toshiba C350 Fire TV", 75: "Toshiba C350 Fire TV"}
LEADER_FAMILIES = set(TV_LEADER.values())
TIER = {"budget": (1.00, 1.30), "mainstream": (1.45, 2.20), "miniled": (2.30, 3.40), "oled_mid": (3.70, 5.40), "oled_prem": (5.50, 8.00)}
GEN = {2025: 1.0, 2024: 0.94, 2023: 0.86, 2022: 0.78}
TVF = [
    ("Samsung", "QN90D Neo QLED", "QN{s}QN90D", "miniled", 2024, [55, 65, 75, 85], True),
    ("Samsung", "QN85D Neo QLED", "QN{s}QN85D", "miniled", 2024, [55, 65, 75, 85], True),
    ("Samsung", "Q80D QLED", "QN{s}Q80D", "mainstream", 2024, [55, 65, 75, 85], True),
    ("Samsung", "Q70D QLED", "QN{s}Q70D", "mainstream", 2024, [55, 65, 75, 85], True),
    ("Samsung", "Q60D QLED", "QN{s}Q60D", "mainstream", 2024, [43, 50, 55, 65, 75], True),
    ("Samsung", "S90D OLED", "QN{s}S90D", "oled_mid", 2024, [55, 65, 77], True),
    ("Samsung", "Crystal UHD DU7200", "UN{s}DU7200", "budget", 2024, [43, 50, 55, 65, 75], False),
    ("Samsung", "The Frame LS03D", "QN{s}LS03D", "miniled", 2024, [32, 43, 50, 55, 65, 75, 85], False),
    ("Samsung", "QN90C Neo QLED", "QN{s}QN90C", "miniled", 2023, [43, 55, 65, 75, 85], False),
    ("Samsung", "Q60C QLED", "QN{s}Q60C", "mainstream", 2023, [43, 50, 55, 65, 75], False),
    ("Samsung", "S90C OLED", "QN{s}S90C", "oled_mid", 2023, [55, 65, 77], False),
    ("LG", "C4 OLED", "OLED{s}C4", "oled_mid", 2024, [42, 48, 55, 65, 77], False),
    ("LG", "G4 OLED evo", "OLED{s}G4", "oled_prem", 2024, [55, 65, 77], False),
    ("LG", "B4 OLED", "OLED{s}B4", "oled_mid", 2024, [55, 65, 77], False),
    ("LG", "QNED85", "{s}QNED85", "mainstream", 2024, [50, 55, 65, 75, 86], False),
    ("LG", "UT8000", "{s}UT8000", "budget", 2024, [43, 50, 55, 65, 75], False),
    ("LG", "C3 OLED", "OLED{s}C3", "oled_mid", 2023, [42, 48, 55, 65, 77], False),
    ("LG", "G3 OLED evo", "OLED{s}G3", "oled_prem", 2023, [55, 65, 77], False),
    ("LG", "B3 OLED", "OLED{s}B3", "oled_mid", 2023, [55, 65, 77], False),
    ("LG", "UQ7570", "{s}UQ7570", "budget", 2022, [43, 50, 55, 65, 75], False),
    ("LG", "NANO75", "{s}NANO75UQA", "budget", 2022, [43, 50, 55, 65, 75, 86], False),
    ("Sony", "Bravia 7", "K-{s}XR70", "miniled", 2024, [55, 65, 75, 85], False),
    ("Sony", "Bravia 8 OLED", "K-{s}XR80", "oled_prem", 2024, [55, 65], False),
    ("Sony", "Bravia 3", "K-{s}S30", "mainstream", 2024, [43, 50, 55, 65, 75], False),
    ("Sony", "Bravia A80L OLED", "XR-{s}A80L", "oled_mid", 2023, [55, 65, 77], False),
    ("Sony", "Bravia X90L", "XR-{s}X90L", "miniled", 2023, [55, 65, 75, 85], False),
    ("Sony", "Bravia X77L", "KD-{s}X77L", "budget", 2023, [43, 50, 55, 65, 75], False),
    ("TCL", "QM7", "{s}QM7", "miniled", 2024, [55, 65, 75, 85], False),
    ("TCL", "QM8", "{s}QM8", "miniled", 2024, [65, 75, 85], False),
    ("TCL", "Q6", "{s}Q650", "mainstream", 2024, [43, 50, 55, 65, 75], False),
    ("TCL", "S4", "{s}S450", "budget", 2024, [43, 50, 55, 65], False),
    ("TCL", "Q7", "{s}Q750G", "mainstream", 2023, [55, 65, 75, 85], False),
    ("TCL", "5-Series S555", "{s}S555", "budget", 2023, [43, 50, 55, 65, 75], False),
    ("Hisense", "U6N", "{s}U6N", "mainstream", 2024, [55, 65, 75, 85], False),
    ("Hisense", "U7N", "{s}U7N", "miniled", 2024, [55, 65, 75], False),
    ("Hisense", "U8N", "{s}U8N", "miniled", 2024, [55, 65, 75], False),
    ("Hisense", "A6", "{s}A65H", "budget", 2024, [43, 50, 55, 65, 75], False),
    ("Hisense", "QD7", "{s}QD7N", "mainstream", 2024, [43, 50, 55, 65, 75, 85], False),
    ("Hisense", "U6K", "{s}U6K", "mainstream", 2023, [55, 65, 75, 85], False),
    ("Hisense", "U8K", "{s}U8K", "miniled", 2023, [55, 65, 75], False),
    ("Vizio", "V-Series", "V{s}M-K", "budget", 2024, [43, 50, 55, 65, 75], False),
    ("Vizio", "MQX", "M{s}QXM-K", "mainstream", 2024, [55, 65, 75], False),
    ("Vizio", "V4K", "V4K{s}M-K", "budget", 2023, [43, 50, 55, 65, 75], False),
    ("Roku", "Plus Series", "{s}R6B5", "mainstream", 2024, [43, 50, 55, 65, 75], False),
    ("Amazon", "Fire TV Omni QLED", "{s}-QL-F", "mainstream", 2024, [43, 50, 55, 65, 75], False),
    ("Insignia", "F50 Fire TV", "NS-{s}F501NA", "budget", 2024, [32, 43, 50, 55, 65, 75], False),
    ("Toshiba", "C350 Fire TV", "{s}C350", "budget", 2024, [32, 43, 50, 55, 65, 75], False),
]
PRICE_UP_FAMILIES = {"Motorola Moto G 5G (2024)", "Motorola Moto G Power 5G (2024)"}  # mirror the sourced Moto G (2026) list-price increase
STOR_ADD = {64: -100, 128: 0, 256: 100, 512: 300, 1024: 500}
# (brand, model, msrp_base, storages, colors, released_year, klass)   klass: apple|flagship|budget
PHONES = [
    ("Apple", "iPhone 13", 499, [128, 256], ["Midnight", "Starlight", "Blue", "Pink"], 2021.7, "apple"),
    ("Apple", "iPhone 14", 599, [128, 256], ["Midnight", "Blue", "Starlight", "Purple"], 2022.7, "apple"),
    ("Apple", "iPhone SE (3rd gen)", 429, [64, 128, 256], ["Midnight", "Starlight", "Red"], 2022.2, "apple"),
    ("Apple", "iPhone 15", 699, [128, 256, 512], ["Black", "Blue", "Pink", "Green"], 2023.7, "apple"),
    ("Apple", "iPhone 15 Plus", 799, [128, 256, 512], ["Black", "Blue", "Yellow"], 2023.7, "apple"),
    ("Apple", "iPhone 16", 799, [128, 256, 512], ["Black", "White", "Teal", "Ultramarine"], 2024.7, "apple"),
    ("Apple", "iPhone 16 Plus", 899, [128, 256, 512], ["Black", "White", "Pink"], 2024.7, "apple"),
    ("Apple", "iPhone 16e", 599, [128, 256, 512], ["Black", "White"], 2025.1, "apple"),
    ("Apple", "iPhone 16 Pro", 999, [128, 256, 512, 1024], ["Black Titanium", "Natural Titanium", "Desert Titanium"], 2024.7, "apple"),
    ("Apple", "iPhone 16 Pro Max", 1199, [256, 512, 1024], ["Black Titanium", "Natural Titanium", "White Titanium"], 2024.7, "apple"),
    ("Samsung", "Galaxy S23 FE", 599, [128, 256], ["Mint", "Cream", "Graphite", "Purple"], 2023.8, "flagship"),
    ("Samsung", "Galaxy S24", 799, [128, 256], ["Onyx Black", "Marble Gray", "Cobalt Violet"], 2024.0, "flagship"),
    ("Samsung", "Galaxy S24+", 999, [256, 512], ["Onyx Black", "Marble Gray", "Amber Yellow"], 2024.0, "flagship"),
    ("Samsung", "Galaxy S24 Ultra", 1299, [256, 512, 1024], ["Titanium Black", "Titanium Gray", "Titanium Violet"], 2024.0, "flagship"),
    ("Samsung", "Galaxy S25", 799, [128, 256], ["Navy", "Icyblue", "Mint"], 2025.1, "flagship"),
    ("Samsung", "Galaxy Z Flip6", 1099, [256, 512], ["Blue", "Silver Shadow", "Yellow", "Mint"], 2024.5, "flagship"),
    ("Samsung", "Galaxy A15 5G", 199, [128], ["Blue Black", "Light Blue", "Blue"], 2023.9, "budget"),
    ("Samsung", "Galaxy A16 5G", 199, [128], ["Black", "Light Gray", "Light Green"], 2024.8, "budget"),
    ("Samsung", "Galaxy A35 5G", 399, [128, 256], ["Awesome Navy", "Awesome Lemon"], 2024.2, "budget"),
    ("Samsung", "Galaxy A54 5G", 449, [128, 256], ["Awesome Graphite", "Awesome Lime"], 2023.2, "budget"),
    ("Google", "Pixel 7a", 449, [128], ["Charcoal", "Snow", "Sea"], 2023.4, "flagship"),
    ("Google", "Pixel 8", 699, [128, 256], ["Obsidian", "Hazel", "Rose"], 2023.8, "flagship"),
    ("Google", "Pixel 8a", 499, [128, 256], ["Obsidian", "Bay", "Aloe"], 2024.4, "flagship"),
    ("Google", "Pixel 9", 799, [128, 256], ["Obsidian", "Porcelain", "Wintergreen"], 2024.6, "flagship"),
    ("Google", "Pixel 9 Pro", 999, [128, 256, 512], ["Obsidian", "Porcelain", "Hazel"], 2024.6, "flagship"),
    ("Google", "Pixel 9 Pro XL", 1099, [128, 256, 512], ["Obsidian", "Porcelain", "Hazel"], 2024.6, "flagship"),
    ("Motorola", "Moto G Power 5G (2024)", 299, [128], ["Midnight Blue", "Mauve"], 2024.0, "budget"),
    ("Motorola", "Moto G Stylus 5G (2024)", 399, [128, 256], ["Cosmic Blue", "Scarlet Wave"], 2024.3, "budget"),
    ("Motorola", "Moto G 5G (2024)", 199, [128], ["Sage Green", "Ocean Blue"], 2024.0, "budget"),
    ("Motorola", "Moto Edge (2024)", 549, [256], ["Eclipse Black", "Jade Green"], 2024.5, "budget"),
    ("OnePlus", "OnePlus 12", 799, [256, 512], ["Silky Black", "Flowy Emerald"], 2024.0, "flagship"),
    ("OnePlus", "OnePlus 12R", 499, [128, 256], ["Cool Blue", "Iron Gray"], 2024.0, "flagship"),
]
LAPTOPS = [  # brand, model, msrp_base, [(config, add)], released, klass
    ("Apple", "MacBook Air 13 M2", 999, [("8GB/256GB", 0), ("8GB/512GB", 200), ("16GB/512GB", 400)], 2022.5, "apple"),
    ("Apple", "MacBook Air 13 M3", 1099, [("8GB/256GB", 0), ("16GB/512GB", 400)], 2024.2, "apple"),
    ("Apple", "MacBook Air 15 M3", 1299, [("8GB/256GB", 0), ("16GB/512GB", 400)], 2024.2, "apple"),
    ("Apple", "MacBook Pro 14 M3", 1599, [("8GB/512GB", 0), ("16GB/1TB", 400)], 2023.8, "apple"),
    ("Dell", "XPS 13 9340", 1199, [("16GB/512GB", 0), ("32GB/1TB", 500)], 2024.2, "pc"),
    ("Dell", "Inspiron 15 3530", 549, [("8GB/256GB", 0), ("16GB/512GB", 150)], 2023.5, "pc"),
    ("Dell", "Inspiron 14 Plus 7440", 849, [("16GB/512GB", 0), ("16GB/1TB", 130)], 2024.2, "pc"),
    ("Dell", "Latitude 5450", 1099, [("16GB/512GB", 0), ("32GB/1TB", 400)], 2024.5, "pc"),
    ("HP", "Pavilion 15", 599, [("8GB/256GB", 0), ("16GB/512GB", 150)], 2023.5, "pc"),
    ("HP", "Envy x360 14", 899, [("16GB/512GB", 0), ("16GB/1TB", 150)], 2024.0, "pc"),
    ("HP", "OmniBook 5 14", 749, [("16GB/512GB", 0), ("16GB/1TB", 130)], 2024.5, "pc"),
    ("HP", "15-fd Laptop", 529, [("8GB/256GB", 0), ("16GB/512GB", 130), ("16GB/1TB", 230)], 2024.0, "pc"),
    ("Lenovo", "IdeaPad Slim 3 15", 449, [("8GB/256GB", 0), ("16GB/512GB", 130)], 2024.0, "pc"),
    ("Lenovo", "IdeaPad 5 15", 649, [("8GB/512GB", 0), ("16GB/512GB", 100), ("16GB/1TB", 200)], 2024.0, "pc"),
    ("Lenovo", "ThinkPad E14 Gen 6", 849, [("16GB/512GB", 0), ("32GB/1TB", 350)], 2024.5, "pc"),
    ("Lenovo", "Yoga 7i 2-in-1 14", 899, [("16GB/512GB", 0), ("16GB/1TB", 150)], 2024.0, "pc"),
    ("ASUS", "Zenbook 14 OLED", 999, [("16GB/512GB", 0), ("16GB/1TB", 150)], 2024.0, "pc"),
    ("ASUS", "Vivobook 15", 499, [("8GB/256GB", 0), ("16GB/512GB", 140)], 2023.8, "pc"),
    ("ASUS", "TUF Gaming A15", 899, [("16GB/512GB", 0), ("16GB/1TB", 130)], 2024.0, "pc"),
    ("Acer", "Aspire 5 15", 549, [("8GB/256GB", 0), ("16GB/512GB", 140)], 2023.8, "pc"),
    ("Acer", "Swift Go 14", 799, [("16GB/512GB", 0), ("16GB/1TB", 130)], 2024.0, "pc"),
    ("Acer", "Nitro V 15", 799, [("16GB/512GB", 0), ("16GB/1TB", 130)], 2024.2, "pc"),
    ("Microsoft", "Surface Laptop 7 13.8in", 999, [("16GB/256GB", 0), ("16GB/512GB", 200), ("32GB/1TB", 600)], 2024.5, "pc"),
    ("Samsung", "Galaxy Book4 15.6", 699, [("8GB/256GB", 0), ("16GB/512GB", 180)], 2024.1, "pc"),
]
TABLETS = [  # brand, model, msrp_base, storages, colors, released, klass
    ("Apple", "iPad (10th gen)", 349, [64, 256], ["Blue", "Pink", "Silver", "Yellow"], 2022.8, "apple"),
    ("Apple", "iPad Air 11in M2", 599, [128, 256, 512], ["Space Gray", "Blue", "Purple", "Starlight"], 2024.4, "apple"),
    ("Apple", "iPad mini (A17 Pro)", 499, [128, 256, 512], ["Space Gray", "Blue", "Purple", "Starlight"], 2024.8, "apple"),
    ("Apple", "iPad Pro 11in M4", 999, [256, 512, 1024], ["Space Black", "Silver"], 2024.4, "apple"),
    ("Samsung", "Galaxy Tab A9+", 219, [64, 128], ["Graphite", "Silver"], 2023.8, "budget"),
    ("Samsung", "Galaxy Tab S9 FE", 449, [128, 256], ["Gray", "Mint", "Lavender"], 2023.8, "flagship"),
    ("Samsung", "Galaxy Tab S10+", 999, [256, 512], ["Moonstone Gray", "Platinum Silver"], 2024.8, "flagship"),
    ("Amazon", "Fire HD 8", 99, [32, 64], ["Black", "Denim"], 2024.4, "budget"),
    ("Amazon", "Fire HD 10", 139, [32, 64], ["Black", "Lavender"], 2023.1, "budget"),
    ("Amazon", "Fire Max 11", 229, [64, 128], ["Gray"], 2023.4, "budget"),
    ("Lenovo", "Tab M10 (3rd gen)", 159, [64, 128], ["Storm Gray"], 2022.5, "budget"),
    ("Google", "Pixel Tablet", 499, [128, 256], ["Porcelain", "Hazel"], 2023.5, "flagship"),
]
WASHERS = [  # brand, model line, msrp, colors, segment, released
    ("Insignia", "3.7 cu ft Top Load", 599.99, ["White"], "top_load", 2024.0),
    ("LG", "WM4000H 4.5 cu ft Front Load", 1100, ["White", "Graphite Steel"], "front_load", 2023.0),
    ("LG", "WT7300C 5.0 cu ft Top Load", 900, ["White", "Middle Black"], "top_load", 2023.0),
    ("Samsung", "WF45B6300 4.5 cu ft Front Load", 1000, ["White", "Brushed Black"], "front_load", 2022.5),
    ("Samsung", "WA50R5200 5.0 cu ft Top Load", 750, ["White", "Platinum"], "top_load", 2021.0),
    ("Whirlpool", "WTW5000D 4.3 cu ft Top Load", 800, ["White"], "top_load", 2022.0),
    ("Whirlpool", "WFW5605M 4.5 cu ft Front Load", 950, ["White", "Chrome Shadow"], "front_load", 2022.0),
    ("Whirlpool", "WTW7120H 5.3 cu ft Top Load", 950, ["White", "Chrome Shadow"], "top_load", 2023.0),
    ("Maytag", "MVW6230H 5.3 cu ft Top Load", 1050, ["White", "Metallic Slate"], "top_load", 2022.0),
    ("Maytag", "MHW5630H 4.8 cu ft Front Load", 1150, ["White", "Metallic Slate"], "front_load", 2022.0),
    ("GE", "GTW500AS 4.5 cu ft Top Load", 750, ["White"], "top_load", 2021.5),
    ("GE", "GFW550S 4.8 cu ft Front Load", 1000, ["White", "Diamond Gray"], "front_load", 2022.0),
    ("Electrolux", "EFLS527U 4.4 cu ft Front Load", 1100, ["White", "Titanium"], "front_load", 2021.0),
    ("Frigidaire", "FFFW6000 4.5 cu ft Front Load", 850, ["White", "Titanium"], "front_load", 2022.5),
    ("Frigidaire", "FFTW4120 4.1 cu ft Top Load", 650, ["White"], "top_load", 2022.5),
    ("Speed Queen", "TR5003WN 3.2 cu ft Top Load", 1250, ["White"], "top_load", 2020.0),
    ("Kenmore", "41262 4.5 cu ft Top Load", 700, ["White", "Black"], "top_load", 2022.0),
]
WASHER_DRYERS = [
    ("LG", "WM3998H 4.2 cu ft Ventless Combo", 1700, ["White", "Graphite Steel"], "combo", 2022.5),
    ("GE Profile", "UltimateFit 4.8 cu ft Combo", 1900, ["White", "Carbon Graphite"], "combo", 2023.0),
    ("Samsung", "Bespoke AI Laundry Combo 5.3 cu ft", 2000, ["White", "Brushed Black"], "combo", 2024.0),
    ("Whirlpool", "4.5 cu ft Combo Washer/Dryer", 1500, ["White", "Chrome Shadow"], "combo", 2022.0),
    ("Electrolux", "4.4 cu ft Ventless Combo", 1800, ["White", "Titanium"], "combo", 2022.0),
    ("Haier", "2.0 cu ft Compact Combo", 1300, ["White", "Graphite"], "combo", 2021.0),
    ("Kenmore", "4.2 cu ft Ventless Combo", 1400, ["White", "Silver"], "combo", 2022.0),
]
FRIDGES = [
    ("Whirlpool", "18 cu ft Top Freezer", 849, ["White", "Stainless"], "top_freezer", 2022.0),
    ("Frigidaire", "18.3 cu ft Top Freezer", 749, ["White", "Stainless"], "top_freezer", 2022.0),
    ("GE", "17.5 cu ft Top Freezer", 799, ["White", "Stainless"], "top_freezer", 2021.5),
    ("Hisense", "17 cu ft Top Freezer", 649, ["White", "Stainless"], "top_freezer", 2023.0),
    ("Samsung", "25 cu ft Side by Side", 1599, ["Stainless", "Black Stainless"], "side_by_side", 2022.5),
    ("LG", "27 cu ft Side by Side", 1699, ["Stainless", "Black Stainless"], "side_by_side", 2022.5),
    ("Frigidaire", "25.6 cu ft Side by Side", 1399, ["Stainless", "White"], "side_by_side", 2022.0),
    ("Whirlpool", "25 cu ft Side by Side", 1499, ["Stainless", "White"], "side_by_side", 2023.0),
    ("LG", "29 cu ft French Door", 2899, ["Stainless", "Black Stainless"], "french_door", 2023.0),
    ("Samsung", "28 cu ft French Door", 2599, ["Stainless", "Black Stainless"], "french_door", 2023.0),
    ("GE", "27.7 cu ft French Door", 2399, ["Stainless", "Fingerprint Resistant"], "french_door", 2022.5),
    ("Whirlpool", "36in 25 cu ft French Door", 2199, ["Stainless", "Black"], "french_door", 2023.0),
    ("Maytag", "25 cu ft French Door", 2299, ["Stainless", "Black"], "french_door", 2022.5),
]
# accessories: (type, floor_street, brand->tier, variants, seasonal, compat)  compat in {None,'phone','tablet','laptop','tv'}
BR = {"SanDisk": "mid", "PNY": "budget", "Lexar": "budget", "Crucial": "mid", "WD": "mid", "TP-Link": "mid", "Wyze": "budget", "Roku": "mid",
      "Amazon": "budget", "Google": "mid", "Vizio": "budget", "Affresh": "mid", "Anker": "mid", "Belkin": "mid", "Amazon Basics": "budget", "Insignia": "budget", "UGREEN": "budget", "Cable Matters": "budget",
      "Spigen": "mid", "OtterBox": "prem", "Speck": "mid", "ZAGG": "mid", "amFilm": "budget", "Logitech": "mid", "Sony": "prem",
      "JBL": "mid", "Echogear": "mid", "Sanus": "prem", "Mounting Dream": "budget", "Philips": "mid", "Honeywell": "mid",
      "Lasko": "mid", "Dyson": "prem", "Brita": "mid", "Whirlpool": "mid", "Frigidaire": "mid", "Samsung": "mid", "LG": "mid", "TCL": "budget",
      "Hisense": "budget", "Apple": "prem"}
ACC_FIXED = [
    ("USB-C Cable", 5.99, ["Anker", "Belkin", "Amazon Basics", "Insignia", "UGREEN", "Cable Matters"], ["1ft", "3ft", "6ft", "10ft"] , 0, None),
    ("Lightning Cable", 9.99, ["Anker", "Belkin", "Amazon Basics", "UGREEN"], ["1ft", "3ft", "6ft", "10ft"], 0, None),
    ("HDMI 2.1 Cable", 7.99, ["Anker", "Belkin", "Amazon Basics", "Insignia", "UGREEN", "Cable Matters"], ["3ft", "6ft", "10ft", "15ft", "25ft"], 0, None),
    ("USB-C Wall Charger", 12.99, ["Anker", "Belkin", "Amazon Basics", "UGREEN", "Insignia"], ["20W", "30W", "45W", "65W GaN", "100W GaN"], 0, None),
    ("Power Bank", 19.99, ["Anker", "Belkin", "UGREEN", "Insignia"], ["10000mAh", "20000mAh", "26800mAh"], 0, None),
    ("Wireless Earbuds", 19.99, ["Anker", "JBL", "Sony", "Insignia", "Belkin"], ["Black", "White", "Blue"], 0, None),
    ("Bluetooth Speaker", 24.99, ["JBL", "Anker", "Sony", "Insignia"], ["Mini", "Mid", "Large"], 0, None),
    ("Wireless Mouse", 9.99, ["Logitech", "Amazon Basics", "Insignia", "Anker"], ["Black", "White", "Graphite"], 0, None),
    ("Wireless Keyboard", 14.99, ["Logitech", "Amazon Basics", "Insignia", "Anker"], ["Black", "White", "Graphite"], 0, None),
    ("Surge Protector", 11.99, ["Belkin", "Amazon Basics", "Insignia", "Anker"], ["6-outlet", "8-outlet", "12-outlet"], 0, None),
    ("TV Wall Mount", 24.99, ["Echogear", "Sanus", "Mounting Dream", "Insignia"], ["Fixed 32-55in", "Tilt 37-70in", "Full-Motion 37-75in", "Full-Motion 42-90in"], 0, None),
    ("Replacement TV Remote", 9.99, ["Samsung", "LG", "Sony", "TCL", "Hisense", "Insignia"], ["Universal", "Voice", "Standard"], 0, None),
    ("Laptop Sleeve", 12.99, ["Amazon Basics", "Belkin", "Anker", "Insignia"], ["13in Black", "13in Gray", "14in Black", "14in Gray", "15.6in Black", "15.6in Gray", "16in Black"], 0, None),
    ("Washer Fill Hose Pair", 12.99, ["Whirlpool", "Frigidaire", "Amazon Basics", "Insignia"], ["4ft", "6ft", "Braided 6ft"], 0, None),
    ("Dryer Vent Kit", 14.99, ["Whirlpool", "Frigidaire", "Amazon Basics"], ["4in x 8ft", "4in x 12ft"], 0, None),
    ("Refrigerator Water Filter", 29.99, ["Whirlpool", "Frigidaire", "Samsung", "LG", "Brita"], ["Standard", "2-pack", "3-pack"], 0, None),
    ("Portable Fan", 19.99, ["Honeywell", "Lasko", "Dyson", "Amazon Basics"], ["Desk", "Tower", "Pedestal", "Clip"], 1, None),
    ("Space Heater", 29.99, ["Honeywell", "Lasko", "Dyson", "Amazon Basics"], ["Ceramic 1500W", "Oscillating", "Tower", "Mini"], 1, None),
    ("Air Purifier Filter", 17.99, ["Honeywell", "Philips", "Amazon Basics"], ["HEPA Small", "HEPA Medium", "HEPA Large"], 0, None),
]
ACC_NEW = [
    ("Ethernet Cable Cat6", 5.99, ["Amazon Basics", "UGREEN", "Cable Matters", "Insignia", "Anker"], ["3ft", "7ft", "15ft", "25ft", "50ft"], 0),
    ("microSD Card", 7.99, ["SanDisk", "Samsung", "PNY", "Lexar"], ["64GB", "128GB", "256GB", "512GB", "1TB"], 0),
    ("Portable SSD", 49.99, ["SanDisk", "Samsung", "Crucial", "WD"], ["500GB", "1TB", "2TB", "4TB"], 0),
    ("USB Flash Drive", 5.99, ["SanDisk", "Samsung", "PNY", "Lexar"], ["32GB", "64GB", "128GB", "256GB", "512GB"], 0),
    ("Webcam", 24.99, ["Logitech", "Insignia", "Anker"], ["720p", "1080p", "4K"], 0),
    ("USB-C Hub", 19.99, ["Anker", "Belkin", "UGREEN", "Insignia"], ["4-in-1", "7-in-1", "9-in-1", "11-in-1"], 0),
    ("Laptop Stand", 14.99, ["Amazon Basics", "UGREEN", "Anker"], ["Aluminum Silver", "Aluminum Gray", "Foldable Black", "Foldable White"], 0),
    ("Monitor Arm", 24.99, ["Amazon Basics", "Echogear", "Mounting Dream"], ["Single", "Dual", "Gas Spring Single", "Gas Spring Dual"], 0),
    ("Smart Plug", 8.99, ["Amazon Basics", "TP-Link", "Wyze"], ["1-pack", "2-pack", "4-pack"], 0),
    ("Streaming Stick", 24.99, ["Roku", "Amazon", "Google"], ["HD", "4K", "4K Max"], 0),
    ("Soundbar", 69.99, ["Samsung", "LG", "Sony", "Roku", "Vizio", "JBL"], ["2.0", "2.1", "3.1", "5.1", "5.1.2 Atmos"], 0),
    ("Car Charger", 9.99, ["Anker", "Belkin", "UGREEN", "Insignia"], ["20W", "30W", "45W Dual"], 0),
    ("Wireless Charging Pad", 14.99, ["Anker", "Belkin", "Spigen", "Insignia"], ["10W", "15W", "15W Stand", "3-in-1"], 0),
    ("Over-Ear Headphones", 29.99, ["Sony", "JBL", "Anker", "Insignia", "Logitech"], ["Black", "White", "Blue", "Gray"], 0),
    ("Washer Cleaner Tablets", 6.99, ["Affresh", "Whirlpool", "Amazon Basics"], ["6-count", "12-count", "24-count"], 0),
    ("Refrigerator Air Filter", 9.99, ["Whirlpool", "Frigidaire", "Samsung", "LG"], ["Standard", "2-pack", "3-pack"], 0),
]
COLORS_FOR = {"USB-C Cable": ["Black", "White"], "Lightning Cable": ["Black", "White"], "USB-C Wall Charger": ["Black", "White"],
              "Power Bank": ["Black", "White", "Blue"], "Bluetooth Speaker": ["Black", "Blue", "Red"], "Surge Protector": ["White", "Black"]}
PACKS_FOR = {"USB-C Cable": ["1-pack", "2-pack"], "Lightning Cable": ["1-pack", "2-pack"], "HDMI 2.1 Cable": ["1-pack", "2-pack"]}
PACK_MULT = {None: 1.0, "1-pack": 1.0, "2-pack": 1.7}
PHONE_CASES = {"Apple": ["Spigen", "OtterBox", "Speck", "Apple", "Belkin"], "Samsung": ["Spigen", "OtterBox", "Speck", "Samsung"],
               "Google": ["Spigen", "OtterBox", "Speck"], "Motorola": ["Spigen", "Amazon Basics"], "OnePlus": ["Spigen", "Amazon Basics"]}
ACC_CAT = {}


def slug(s, n=14):
    return "".join(c for c in str(s).upper() if c.isalnum())[:n]


def mrng(*key):
    """Per-key deterministic RNG, independent of iteration order."""
    return random.Random("|".join(str(k) for k in key))


def build_catalog(seed):
    cat = {k: [] for k in ("tv", "phone", "laptop", "tablet", "washer", "washer_dryer", "refrigerator", "accessory")}
    for brand, fam, pat, tier, year, sizes, launch in TVF:
        for s in sizes:
            model = pat.format(s=s)
            cat["tv"].append(dict(category="tv", brand=brand, model=model, family=f"{brand} {fam}", launch=launch, tier=tier,
                                  year=year, size=s, subcat="TVs", sku=f"TV-{slug(brand, 3)}-{slug(model)}"))
    for brand, model, base, stors, colors, rel, kl in PHONES:
        for st in stors:
            for col in colors:
                stl = f"{st}GB" if st < 1024 else "1TB"
                cat["phone"].append(dict(category="phone", brand=brand, model=f"{model} {stl} {col}", msrp=base + STOR_ADD[st], rel=rel, klass=kl,
                                         family=f"{brand} {model}", launch=False, subcat="Phones", sku=f"PH-{slug(brand, 3)}-{slug(model)}-{stl}-{slug(col, 4)}"))
    for brand, model, base, cfgs, rel, kl in LAPTOPS:
        for lab, add in cfgs:
            cat["laptop"].append(dict(category="laptop", brand=brand, model=f"{model} {lab}", msrp=base + add, rel=rel, klass=kl,
                                      family=f"{brand} {model}", launch=False, subcat="Laptops", sku=f"LT-{slug(brand, 3)}-{slug(model)}-{slug(lab, 7)}"))
    for brand, model, base, stors, colors, rel, kl in TABLETS:
        for st in stors:
            for col in colors:
                stl = f"{st}GB"
                cat["tablet"].append(dict(category="tablet", brand=brand, model=f"{model} {stl} {col}", msrp=base + (st // 64 - 1) * 50, rel=rel, klass=kl,
                                          family=f"{brand} {model}", launch=False, subcat="Tablets", sku=f"TB-{slug(brand, 3)}-{slug(model)}-{stl}-{slug(col, 4)}"))
    for key, rows, pre, sub in (("washer", WASHERS, "WS", "Washers"), ("washer_dryer", WASHER_DRYERS, "WD", "Washer-Dryers"), ("refrigerator", FRIDGES, "RF", "Refrigerators")):
        for brand, model, base, colors, seg, rel in rows:
            for col in colors:
                cat[key].append(dict(category=key, brand=brand, model=f"{model} {col}", msrp=base, rel=rel, klass="appliance", seg=seg,
                                     family=f"{brand} {model}", launch=False, subcat=sub, sku=f"{pre}-{slug(brand, 3)}-{slug(model, 9)}-{slug(col, 4)}"))
    # accessories
    def add_acc(typ, floor, brand, variant, seasonal, compat=None, fam=None):
        cat["accessory"].append(dict(category="accessory", brand=brand, model=f"{typ} {variant}", acc_type=typ, floor=floor, btier=BR.get(brand, "mid"),
                                     seasonal=seasonal, compat=compat, family=fam or f"{brand} {typ}", launch=False, subcat=typ,
                                     sku=f"AC-{slug(typ, 6)}-{slug(brand, 4)}-{slug(variant, 16)}"))
    for typ, floor, brands, variants, seasonal in [(t, f, b, v, se) for t, f, b, v, se, _ in ACC_FIXED] + ACC_NEW:
        for b in brands:
            for v in variants:
                for col in COLORS_FOR.get(typ, [None]):
                    for pk in PACKS_FOR.get(typ, [None]):
                        label = " ".join(x for x in (v, col, pk) if x)
                        add_acc(typ, round(floor * PACK_MULT[pk], 2), b, label, seasonal)
    for brand, model, base, stors, colors, rel, kl in PHONES:
        for b in PHONE_CASES[brand]:
            styles = ["Clear", "Rugged Black", "Rugged Navy"] + (["Wallet"] if brand in ("Apple", "Samsung") else [])
            for style in styles:
                add_acc("Phone Case", 9.99, b, f"{style} for {model}", 0, "phone", f"{brand} {model}")
        for b in ("ZAGG", "Spigen", "amFilm"):
            for style in ("Tempered Glass", "Privacy"):
                for pk in ("1-pack", "2-pack"):
                    add_acc("Screen Protector", round(6.99 * PACK_MULT[pk], 2), b, f"{style} {pk} for {model}", 0, "phone", f"{brand} {model}")
    for brand, model, base, stors, colors, rel, kl in TABLETS:
        for b in ("Spigen", "Amazon Basics", "Belkin"):
            add_acc("Tablet Case", 12.99, b, f"for {model}", 0, "tablet", f"{brand} {model}")
    return cat


def pick_items(cat, items, n, rng):
    """Return list of (item, lot). ~4% of rows are a second/third cost-lot of a model already stocked (same model, different
    purchase date and cost), the rest unique variants (launch family kept first). If variants run out, more lots fill the gap."""
    base = items[:]
    if cat in ("tv", "phone"):
        def _must(i):
            return i["launch"] or i.get("family") in PRICE_UP_FAMILIES or (cat == "tv" and i.get("family") in LEADER_FAMILIES)
        must = [i for i in base if _must(i)]
        rest = [i for i in base if not _must(i)]
        rng.shuffle(rest)
        base = must + rest
    else:
        rng.shuffle(base)
    n_lots = max(2, int(n * 0.04))
    n_unique = min(len(base), n - n_lots)
    chosen = [(i, 1) for i in base[:n_unique]]
    lots, k = {}, 0
    pool = base[:max(n_unique, 1)]
    while len(chosen) < n:
        it = pool[(k * 7) % len(pool)]
        k += 1
        lots[it["sku"]] = lots.get(it["sku"], 1) + 1
        chosen.append((it, lots[it["sku"]]))
    return chosen


# ============================================================================== pricing
def retention(klass, years):
    floor, k = {"apple": (0.58, 0.30), "flagship": (0.35, 0.85), "budget": (0.60, 0.40), "pc": (0.55, 0.45), "appliance": (0.85, 0.10)}[klass]
    return floor + (1 - floor) * math.exp(-k * max(years, 0))


_MARKET = {}
HIST_MONTHS = 12


def market_price(item, as_of, seed):
    """Deterministic market scenario for a model: street price today, promo floor today, MSRP, decay, basis."""
    key = f"{item['brand']}|{item['model']}"
    if key in _MARKET:
        return _MARKET[key]
    r = mrng(seed, "price", key)
    cat = item["category"]
    basis, anchor = "msrp_retention", ""
    ev = "estimated"
    if cat == "tv":
        size = item["size"]
        lo, hi = TIER[item["tier"]]
        fam_mult = mrng(seed, "tier", item["family"]).uniform(lo, hi)
        size_boost = (size / 65.0) ** (0.35 if item["tier"] in ("miniled", "oled_mid", "oled_prem") else 0.0)
        gen = GEN[item["year"]] ** (0.5 if item["tier"] == "budget" else 1.0)
        sf, pf = tv_floor(size, "street_floor"), tv_floor(size, "promo_floor")
        street = sf * fam_mult * size_boost * gen * r.uniform(0.97, 1.03)
        street = max(street, sf * mrng(seed, "clamp", item["family"]).uniform(1.0, 1.08))          # per-family, keeps size ordering; anchors are the cheapest REGULAR price at that size
        exact = item["family"] in LEADER_FAMILIES                  # floor-leader lines sit exactly on the anchors at every size
        if exact:
            street = sf
        msrp = street * r.uniform(1.12, 1.55)
        basis = "anchor_size_tier"
        near = min(TV_SIZES_A, key=lambda s: abs(s - size))
        anchor = f"tv_{near}_street"
        ev = A[anchor]["evidence"]
        decay = (0.004 if item["year"] >= 2025 else 0.012 if item["year"] == 2024 else 0.015) + (0.004 if item["tier"] != "budget" else 0)
        promo_floor = pf
    elif cat == "accessory":
        lo, hi = {"budget": (1.0, 1.3), "mid": (1.4, 2.2), "prem": (2.4, 3.6)}[item["btier"]]
        street = item["floor"] * r.uniform(lo, hi)
        promo_floor = item["floor"] * 0.7
        msrp = street * r.uniform(1.05, 1.3)
        basis, anchor, ev = "template_floor", f"acc:{item['acc_type']}", "estimated"
        decay = 0.001
    else:
        years = max(as_of.year + (as_of.timetuple().tm_yday / 365.25) - item["rel"], 0)
        street = item["msrp"] * retention(item["klass"], years) * r.uniform(0.97, 1.03)
        if cat == "phone":
            seg = "budget" if item["msrp"] < 450 else None
            if item["msrp"] < 250:
                seg = "entry"
            fl_id = {"entry": "phone_entry", "budget": "phone_budget"}.get(seg)
        elif cat == "laptop":
            fl_id = "laptop_entry"
        elif cat == "tablet":
            fl_id = "tablet_entry"
        elif cat == "washer":
            fl_id = "washer_top" if item["seg"] == "top_load" else "washer_front"
        elif cat == "washer_dryer":
            fl_id = "washer_dryer"
        else:
            fl_id = {"top_freezer": "fridge_top", "side_by_side": "fridge_side", "french_door": "fridge_french"}[item["seg"]]
        promo_floor = A[f"{fl_id}_promo"]["usd"] if fl_id else street * 0.8
        if fl_id:
            sf = A[f"{fl_id}_street"]["usd"]
            if street < sf:
                street = sf * r.uniform(1.0, 1.06)
            anchor, ev = f"{fl_id}_street", A[f"{fl_id}_street"]["evidence"]
            basis = "msrp_retention_floor_clamped"
        msrp = item["msrp"]
        decay = {"apple": 0.007, "flagship": 0.018, "budget": 0.006, "pc": 0.008, "appliance": 0.003}[item["klass"]]
        if cat == "phone" and item["klass"] == "budget" and (item["family"] in PRICE_UP_FAMILIES or r.random() < 0.05):
            decay, basis = -0.015, basis + "+price_up_trend"   # list/street price rising (Moto G 2026 pattern)
        if cat == "tablet":
            decay = 0.007
    if cat == "tv" and exact:
        street, floor_today = round(sf, 2), round(pf, 2)
    else:
        street = psych(max(street, promo_floor * 1.02))
        floor_today = min(psych(max(promo_floor, street * r.uniform(0.80, 0.93))), street)
    msrp = psych(max(msrp, street))
    hist = [psych(street * (1 + decay) ** m) for m in range(HIST_MONTHS + 1)]
    hist[0] = street
    out = dict(model_key=key, street=street, floor=floor_today, msrp=msrp, basis=basis, anchor=anchor, evidence=ev, decay=decay, hist=hist)
    _MARKET[key] = out
    return out


# ============================================================================== SKU construction
MIX = {"tv": (0.15, 0.14, 0.05), "phone": (0.16, 0.13, 0.05), "laptop": (0.17, 0.14, 0.06), "tablet": (0.16, 0.13, 0.06),
       "washer": (0.17, 0.12, 0.04), "washer_dryer": (0.21, 0.14, 0.07), "refrigerator": (0.17, 0.12, 0.04), "accessory": (0.14, 0.14, 0.05)}
COST_RATIO = {"tv": (0.86, 0.95), "phone": (0.90, 0.96), "laptop": (0.87, 0.94), "tablet": (0.88, 0.95), "washer": (0.76, 0.87),
              "washer_dryer": (0.76, 0.87), "refrigerator": (0.76, 0.87), "accessory": (0.35, 0.55)}
RATE = {"phone": (-1.9, 0.6), "tv": (-2.9, 0.6), "laptop": (-3.2, 0.5), "tablet": (-3.0, 0.6), "washer": (-3.4, 0.5),
        "washer_dryer": (-3.6, 0.5), "refrigerator": (-3.6, 0.5), "accessory": (-1.7, 0.7)}
RATE_CAP = 0.45  # sales/day ceiling so natural sellers never look like velocity outliers (viral_spike fixtures do, on purpose)
QTY_DOS = {"phone": (14, 55), "tv": (18, 60), "laptop": (20, 60), "tablet": (20, 60), "washer": (25, 70), "washer_dryer": (25, 70),
           "refrigerator": (25, 70), "accessory": (30, 90)}


def fixtures(cat):
    """(tag, spec, expected_status). HAND-LABELED expectations: independent of the classifier."""
    slow_d, dead_d, slow_dos, grace = TH[cat]
    f = [
        ("never_sold_new", dict(age=grace - 5, dsls=None, qty=6), "new"),
        ("never_sold_old", dict(age=dead_d + 60, dsls=None, qty=4), "dead"),
        ("never_sold_mid_age", dict(age=(grace + dead_d) // 2, dsls=None, qty=4), "insufficient_data"),
        ("no_received_never_sold", dict(age=None, dsls=None, qty=3), "insufficient_data"),
        ("no_received_but_sold_slow", dict(age=None, dsls=slow_d + 5, qty=3), "slow"),
        ("stockout_recent_sale", dict(age=200, dsls=2, qty=0), "healthy"),
        ("stockout_dead_history", dict(age=300, dsls=dead_d + 30, qty=0), "healthy"),
        ("sold_today_overstock", dict(age=200, dsls=0, qty=min(60, 40), rate=0.03), "slow"),
        ("dsls_equals_slow_threshold", dict(age=slow_d + 60, dsls=slow_d, qty=3), "slow"),
        ("dsls_slow_minus_1", dict(age=slow_d + 60, dsls=slow_d - 1, qty=1), "healthy"),
        ("dsls_equals_dead_threshold", dict(age=dead_d + 60, dsls=dead_d, qty=3), "dead"),
        ("dsls_dead_minus_1", dict(age=dead_d + 60, dsls=dead_d - 1, qty=3), "slow"),
        ("seasonal_past_dead_threshold", dict(age=dead_d + 120, dsls=int(dead_d * 1.2), qty=3, seasonal=1), "slow"),
        ("overpriced_vs_market", dict(age=slow_d + 80, dsls=slow_d + 10, qty=4, price_gap=0.22), "slow"),
        ("cost_above_market_dead", dict(age=dead_d + 90, dsls=dead_d + 40, qty=5, cost_mult=1.22), "dead"),
        ("viral_spike", dict(age=120, dsls=1, qty=5, spike=True), "healthy"),
        ("dead_return_window_open", dict(age=dead_d + 20, dsls=dead_d + 10, qty=4, return_open=True), "dead"),
        ("dead_return_window_closed", dict(age=dead_d + 50, dsls=dead_d + 30, qty=4, return_open=False), "dead"),
    ]
    if int(slow_d * 1.1) < 90:
        f.append(("seasonal_past_slow_threshold", dict(age=slow_d + 60, dsls=int(slow_d * 1.1), qty=1, seasonal=1), "healthy"))
    return f


def make_sku(cat, item, lot, status, spec, as_of, seed, nrng_unused=None):
    r = mrng(seed, "sku", item["sku"], lot)
    slow_d, dead_d, slow_dos, grace = TH[cat]
    fixed = spec is not None
    item_seasonal = int(item.get("seasonal", 0)) if not fixed else int(spec.get("seasonal", 0))
    if item_seasonal and not fixed:
        slow_d, dead_d, slow_dos = int(slow_d * SEASONAL_MULT), int(dead_d * SEASONAL_MULT), int(slow_dos * SEASONAL_MULT)
    mp = market_price(item, as_of, seed)
    slow_type = None
    if fixed:
        age, dsls, qty = spec["age"], spec["dsls"], spec["qty"]
    elif status == "dead":
        age = r.randint(dead_d + 30, 364); dsls = r.randint(dead_d, min(age - 5, dead_d + 170)); qty = r.randint(1, 5 if cat in ("tv", "phone") else 3)
        if cat == "accessory":
            qty = r.randint(3, 40)
    elif status == "slow":
        age = r.randint(slow_d + 40, 360); slow_type = "stopped" if r.random() < 0.5 else "overstock"
        if slow_type == "stopped":
            dsls = r.randint(slow_d, min(dead_d - 1, age - 5)); qty = r.randint(2, 9 if cat in ("tv", "phone") else 5)
            if cat == "accessory":
                qty = r.randint(5, 50)
        else:
            dsls = r.randint(2, 30); qty = 3
    elif status == "new":
        age = r.randint(3, grace - 1); dsls = None if r.random() < 0.6 else r.randint(0, age); qty = r.randint(3, 12)
    else:
        age = r.randint(45, 364); dsls = r.randint(0, min(22, age - 1)); qty = 1
        if r.random() < 0.04:
            qty = 0
    mu, sg = RATE[cat]
    rate = spec.get("rate") if fixed and spec.get("rate") else (0.05 if fixed else math.exp(r.gauss(mu, sg)))
    rate = min(rate, RATE_CAP)
    if status == "dead":
        rate *= 0.7
    if status == "slow" and slow_type == "overstock":
        rate *= 0.35
    received = None if age is None else as_of - timedelta(days=age)
    last_sale = None if dsls is None else as_of - timedelta(days=dsls)
    mb = 6 if age is None else min(HIST_MONTHS, age // 30)
    cost_ratio = r.uniform(*COST_RATIO[cat])
    cost = mp["hist"][mb] * cost_ratio * (spec.get("cost_mult", 1.0) if fixed else 1.0)
    if cat == "accessory":
        cost = max(cost, 0.5)
    cost = round(min(cost, mp["msrp"] * 0.97), 2)   # dealer cost never exceeds list price
    gap = spec.get("price_gap") if fixed and spec.get("price_gap") else r.uniform(-0.03, 0.06)
    if not fixed and status in ("dead", "slow") and r.random() < 0.30:
        gap = r.uniform(0.10, 0.22)                                   # owner holding price above market: a common reason stock stalls
    sell = round(mp["street"] * (1 + gap), 2)
    sell = min(sell, round(mp["msrp"] * 1.15, 2))
    if sell < cost * 1.02:
        sell = round(cost * 1.02, 2)
    seasonal = item_seasonal
    if fixed and "return_open" in spec and spec["return_open"] is not None:
        rw = as_of + timedelta(days=10) if spec["return_open"] else (received or as_of) + timedelta(days=60)
        rw = rw if spec["return_open"] else min(rw, as_of - timedelta(days=1))
    elif received:
        rw = received + timedelta(days=r.choice([30, 45, 60, 90]))
    else:
        rw = None
    return dict(item, status=status, slow_type=slow_type, fixed=fixed, spec=spec, cost=cost, sell=sell, rate=rate, qty=qty, received=received,
                last_sale=last_sale, seasonal=seasonal, return_end=(rw if rw and rw >= as_of else None), mp=mp, lot=lot,
                supplier={"Apple": "Ingram Micro", "Samsung": "Samsung Direct", "LG": "LG Distribution", "Sony": "Sony Direct", "Google": "Ingram Micro"}.get(item["brand"], "Regional Distributor"))


# ============================================================================== sales simulation
def day_mult(d, cat):
    m = 1.25 if d.weekday() >= 5 else 1.0
    if date(2025, 11, 28) <= d <= date(2025, 12, 1):     # Black Friday weekend 2025
        m *= 2.5 if cat in ("tv", "laptop", "tablet", "washer", "washer_dryer", "refrigerator") else 1.8
    elif date(2025, 12, 15) <= d <= date(2025, 12, 24):  # holiday gifting
        m *= 1.4
    return m


def simulate(s, as_of, nrng):
    ev = []
    if s["last_sale"] is None:
        return ev, 0
    start = max(s["received"] or (as_of - timedelta(days=364)), as_of - timedelta(days=364))
    end = max(s["last_sale"], start)
    n = (end - start).days + 1
    days = [start + timedelta(days=i) for i in range(n)]
    rate = s["rate"]
    lam = np.array([rate * day_mult(d, s["category"]) for d in days])
    counts = nrng.poisson(lam)
    counts[-1] = max(int(counts[-1]), 1)
    if s["spec"] and s["spec"].get("spike"):
        for i in range(max(0, n - 20), n - 1):
            counts[i] += int(nrng.poisson(1.5))
    for d, c in zip(days, counts):
        if c > 0:
            unit = s["sell"] * (0.90 if date(2025, 11, 28) <= d <= date(2025, 12, 1) else float(nrng.uniform(0.95, 1.02)))
            ev.append((d, int(c), round(max(unit, 0.5), 2), "sale"))
            rets = int(nrng.binomial(int(c), 0.015))
            if rets:
                rd = min(d + timedelta(days=int(nrng.integers(2, 15))), as_of)
                ev.append((rd, rets, round(unit, 2), "return"))
    return ev, n


def fmt_row(s):
    return [s["sku"], s["brand"], s["model"], s["category"], s["qty"], f'{s["cost"]:.2f}', f'{s["sell"]:.2f}',
            s["last_sale"].isoformat() if s["last_sale"] else "", s["received"].isoformat() if s["received"] else "",
            s["u30"], s["u90"], f'{s["mp"]["msrp"]:.2f}', s["seasonal"], s["supplier"], s["return_end"].isoformat() if s["return_end"] else ""]


HEADER = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "last_sale_date", "received_date",
          "units_sold_30d", "units_sold_90d", "msrp", "is_seasonal", "supplier", "return_window_end"]

SIZES = {
    "demo": {"tv": 200, "phone": 200, "laptop": 50, "washer": 26, "washer_dryer": 14},
    "full": {"tv": 240, "phone": 280, "laptop": 90, "tablet": 80, "washer": 40, "washer_dryer": 20, "refrigerator": 40, "accessory": 2210},
}


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=["demo", "full"], default="demo")
    ap.add_argument("--as-of", default="2026-10-26")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="out")
    ap.add_argument("--anchors", default=None, help="CSV of anchor overrides (same columns as market_price_reference.csv)")
    a = ap.parse_args()
    if a.anchors:
        apply_anchor_overrides(a.anchors)
    as_of = date.fromisoformat(a.as_of)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    nrng = np.random.default_rng(a.seed)
    _MARKET.clear()
    catalog = build_catalog(a.seed)
    counts = SIZES[a.size]

    skus = []
    fixture_cursor = 0
    for cat, n in counts.items():
        picked = pick_items(cat, catalog[cat], n, rng)
        fx = fixtures(cat)
        k = min(len(fx), max(4, int(n * 0.10)))
        # rotate fixture start so every fixture tag appears somewhere even in small categories
        sel = [fx[(fixture_cursor + i) % len(fx)] for i in range(k)]
        fixture_cursor += k
        d_q, s_q, n_q = MIX[cat]
        pool_n = n - k
        nd, ns, nn = round(d_q * pool_n), round(s_q * pool_n), round(n_q * pool_n)
        # launch-family TVs: heavy dead/slow
        statuses = [None] * n
        for i, (it, lot) in enumerate(picked):
            if i >= k and it["launch"] and lot == 1:
                x = rng.random()
                statuses[i] = "dead" if x < 0.4 else "slow" if x < 0.75 else "healthy"
                if statuses[i] == "dead": nd -= 1
                elif statuses[i] == "slow": ns -= 1
        rest = [i for i in range(k, n) if statuses[i] is None]
        pool = ["dead"] * max(nd, 0) + ["slow"] * max(ns, 0) + ["new"] * nn
        pool += ["healthy"] * (len(rest) - len(pool))
        pool = pool[:len(rest)]
        rng.shuffle(pool)
        for i, v in zip(rest, pool):
            statuses[i] = v
        for i, (it, lot) in enumerate(picked):
            if i < k:
                tag, spec, exp = sel[i]
                s = make_sku(cat, it, lot, exp if exp in ("dead", "slow", "new") else "healthy", spec, as_of, a.seed)
                s["fixture_tag"], s["expected"], s["intended"] = tag, exp, exp
            else:
                s = make_sku(cat, it, lot, statuses[i], None, as_of, a.seed)
                s["fixture_tag"], s["expected"], s["intended"] = "", statuses[i], statuses[i]
            skus.append(s)

    # unique SKU codes (lots get -L suffix; residual collisions get numeric suffix)
    seen = set()
    for s in skus:
        code = s["sku"] if s["lot"] == 1 else f'{s["sku"]}-L{s["lot"]}'
        base, n = code, 1
        while code.lower() in seen:
            n += 1
            code = f"{base}-{n}"
        s["sku"] = code
        seen.add(code.lower())
    skus.sort(key=lambda s: s["sku"])

    # sales, velocities, quantities from realised velocity
    sales = []
    for s in skus:
        ev, _ = simulate(s, as_of, nrng)
        s["u30"] = sum(c for d, c, p, t in ev if t == "sale" and (as_of - d).days < 30)
        s["u90"] = sum(c for d, c, p, t in ev if t == "sale" and (as_of - d).days < 90)
        slow_d, dead_d, slow_dos, grace = TH[s["category"]]
        v90 = max(s["u90"], 1) / 90
        lo_d, hi_d = QTY_DOS[s["category"]]
        if not s["fixed"]:
            sd = slow_dos * (SEASONAL_MULT if s["seasonal"] else 1)
            if s["status"] == "healthy" and s["qty"] > 0:
                q = max(1, min(60, int(v90 * r_u(s, "qh", lo_d, min(hi_d, sd * 0.8)))))
                while q / v90 > sd * 0.9 and q > 1:
                    q -= 1
                s["qty"] = q
            elif s["status"] == "slow" and s["slow_type"] == "overstock":
                q = max(3, min(60, int(v90 * r_u(s, "qs", sd + 12, sd + 70)) + 1))
                s["qty"] = q
                if q / v90 <= sd:
                    s["slow_type"] = "stopped"
        for d, c, p, t in ev:
            sales.append((s["sku"], d, c, p, t))

    # computed status + ground truth
    for s in skus:
        s["computed"] = classify(s["qty"], s["received"], s["last_sale"], s["u90"], s["category"], s["seasonal"], as_of)
    # reconcile non-fixture rows whose realized velocity flipped the intended status (keep dataset self-consistent)
    for s in skus:
        if not s["fixed"]:
            s["expected"] = s["computed"] if s["computed"] != s["expected"] else s["expected"]

    # ------------------------------------------------------------------ write files
    write_csv(out / "skus_master.csv", HEADER, [fmt_row(s) for s in skus])
    write_csv(out / "sales_events.csv", ["sku", "event_date", "qty", "unit_price", "event_type"],
              [[k, d.isoformat(), c, f"{p:.2f}", t] for k, d, c, p, t in sorted(sales, key=lambda x: (x[1], x[0], x[4]))])

    mrows = []
    for s in skus:
        mp = s["mp"]
        gap = (s["sell"] - mp["street"]) / mp["street"]
        mrows.append([s["sku"], mp["model_key"], s.get("size", ""), mp["street"], mp["floor"], mp["msrp"], f'{gap:.4f}', int(s["cost"] > mp["street"]),
                      f'{(mp["street"] - s["cost"]) / mp["street"]:.4f}', mp["basis"], mp["anchor"], mp["evidence"]])
    write_csv(out / "market_prices.csv", ["sku", "model_key", "size_inch", "market_street_price", "market_floor_price_today", "msrp_market", "price_gap_pct",
                                          "cost_above_market", "market_margin_at_cost_pct", "price_basis", "anchor_id", "price_evidence"], mrows)

    hist_rows, done = [], set()
    for s in skus:
        mp = s["mp"]
        if mp["model_key"] in done:
            continue
        done.add(mp["model_key"])
        for m, p in enumerate(mp["hist"]):
            hist_rows.append([mp["model_key"], m, (as_of - timedelta(days=30 * m)).isoformat(), p])
    write_csv(out / "market_price_history.csv", ["model_key", "months_ago", "month_date", "market_street_price"], hist_rows)

    write_csv(out / "market_price_reference.csv", ["anchor_id", "category", "segment", "price_type", "usd", "observed_on", "evidence", "source_url", "note"],
              [list(x) for x in ANCHORS])

    gt = []
    for s in skus:
        tags = [s["fixture_tag"]] if s["fixture_tag"] else []
        if s["lot"] > 1: tags.append("extra_cost_lot")
        if s["cost"] > s["mp"]["street"]: tags.append("cost_above_market")
        if s["sell"] > s["mp"]["street"] * 1.10: tags.append("overpriced_vs_market")
        if s["launch"]: tags.append("launch_family")
        if s["return_end"] and s["computed"] == "dead": tags.append("dead_return_open")
        if s["seasonal"]: tags.append("seasonal")
        if s["qty"] == 0: tags.append("zero_qty")
        gt.append([s["sku"], s["intended"], s["expected"], s["computed"], ";".join(dict.fromkeys(tags)), s["family"], int(s["launch"]), DEPT[s["category"]], s["category"], s["subcat"],
                   f'{s["qty"] * s["cost"]:.2f}'])
    write_csv(out / "ground_truth.csv", ["sku", "intended_status", "expected_status", "computed_status", "edge_tags", "family", "launch_family", "department", "category", "subcategory", "capital_at_cost"], gt)

    # demo upload (core hackathon categories only), 199 unique + 1 duplicate with 3 seeded defects
    core = [s for s in skus if s["category"] in ("tv", "phone", "laptop", "washer", "washer_dryer") and not s["fixed"]]
    rr = random.Random(a.seed + 1)
    launch = [s for s in core if s["launch"]]
    others = {c: [s for s in core if s["category"] == c and not s["launch"]] for c in ("tv", "phone", "laptop", "washer", "washer_dryer")}
    for v in others.values(): rr.shuffle(v)
    demo = launch[:30] + others["tv"][:50 - min(len(launch), 30)]
    demo = launch[:30] + others["tv"][:80 - len(launch[:30])]
    for c, n in (("phone", 62), ("laptop", 25), ("washer", 20), ("washer_dryer", 12)):
        demo += others[c][:n]
    used = {x["sku"] for x in demo}
    spare = [x for x in core if x["sku"] not in used and x["category"] in ("phone", "tv", "laptop")]
    rr.shuffle(spare)
    demo += spare[:max(0, 199 - len(demo))]
    demo = demo[:199]
    rows = [fmt_row(s) for s in demo]
    rr.shuffle(rows)
    ix = {h: i for i, h in enumerate(HEADER)}
    rows[7][ix["unit_cost"]] = ""
    rows[41][ix["qty_on_hand"]] = -2
    rows.append(list(rows[93]))
    write_csv(out / "upload_demo_200.csv", HEADER, rows)

    # defects file: 60 clean rows + one injected row per DQ code + formatting edge cases
    clean = [fmt_row(s) for s in skus if not s["fixed"] and s["category"] in ("tv", "phone", "laptop")][:100]
    D, man = [list(r) for r in clean[:60]], []

    def inject(code, fn, note=""):
        row = list(clean[60 + len(man)]); fn(row); D.append(row); man.append([len(D), code, note])
    inject("MISSING_REQUIRED", lambda r: r.__setitem__(ix["brand"], ""))
    inject("MISSING_COST", lambda r: r.__setitem__(ix["unit_cost"], ""))
    inject("NEGATIVE_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], -3))
    row_dup = list(D[2]); D.append(row_dup); man.append([len(D), "DUPLICATE_SKU", "exact duplicate of row 3"])
    inject("FUTURE_DATE", lambda r: r.__setitem__(ix["last_sale_date"], (as_of + timedelta(days=9)).isoformat()))
    inject("PRICE_BELOW_COST", lambda r: r.__setitem__(ix["sell_price"], f'{float(r[ix["unit_cost"]]) * 0.8:.2f}'))
    inject("BAD_CATEGORY", lambda r: r.__setitem__(ix["category"], "televison"))
    inject("SALE_BEFORE_RECEIVED", lambda r: (r.__setitem__(ix["received_date"], (as_of - timedelta(days=10)).isoformat()), r.__setitem__(ix["last_sale_date"], (as_of - timedelta(days=40)).isoformat())))
    inject("VELOCITY_OUTLIER", lambda r: r.__setitem__(ix["units_sold_30d"], 999))
    inject("MSRP_MISMATCH", lambda r: r.__setitem__(ix["sell_price"], f'{float(r[ix["msrp"]]) * 1.5:.2f}'))
    inject("ZERO_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], 0), "info only")
    # formatting edge cases that must PARSE cleanly (no issue expected)
    inject("OK_US_DATE_FORMAT", lambda r: r.__setitem__(ix["last_sale_date"], date.fromisoformat(r[ix["last_sale_date"]]).strftime("%m/%d/%Y") if r[ix["last_sale_date"]] else ""), "must parse")
    inject("OK_CURRENCY_STRING", lambda r: r.__setitem__(ix["unit_cost"], f'${float(r[ix["unit_cost"]]):,.2f}'), "must parse")
    inject("OK_CATEGORY_SYNONYM", lambda r: r.__setitem__(ix["category"], "Television"), "must map to tv")
    inject("OK_FLOAT_QTY", lambda r: r.__setitem__(ix["qty_on_hand"], f'{float(r[ix["qty_on_hand"]]):.1f}'), "must parse")
    write_csv(out / "upload_defects.csv", HEADER, D)
    write_csv(out / "defects_manifest.csv", ["data_row", "expected_code", "note"], man)

    # summary
    summ = {}
    for s in skus:
        c = summ.setdefault(s["category"], dict(n=0, cost=0.0, dead=0.0, slow=0.0, dn=0, sn=0))
        v = s["qty"] * s["cost"]; c["n"] += 1; c["cost"] += v
        if s["computed"] == "dead": c["dead"] += v; c["dn"] += 1
        if s["computed"] == "slow": c["slow"] += v; c["sn"] += 1
    sr, T = [], dict(n=0, cost=0, dead=0, slow=0, dn=0, sn=0)
    for c, v in summ.items():
        sr.append([c, v["n"], round(v["cost"]), v["dn"], round(v["dead"]), f'{v["dead"] / v["cost"]:.1%}', v["sn"], round(v["slow"])])
        for k in T: T[k] += v[k]
    sr.append(["TOTAL", T["n"], round(T["cost"]), T["dn"], round(T["dead"]), f'{T["dead"] / T["cost"]:.1%}', T["sn"], round(T["slow"])])
    write_csv(out / "summary.csv", ["category", "skus", "stock_at_cost", "dead_skus", "dead_capital", "dead_pct", "slow_skus", "slow_capital"], sr)
    print(open(out / "summary.csv").read())


def r_u(s, tag, lo, hi):
    return mrng("qty", s["sku"], tag).uniform(lo, hi)


if __name__ == "__main__":
    main()
