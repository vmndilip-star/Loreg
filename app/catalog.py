"""The product catalog: real product lines (data/catalog_lines.yaml) expanded into one row per variant.

Used by the database generator and by the upload endpoint, so a product has exactly one name everywhere.
A shop upload has no "line" column; its line is found by matching brand and model against this catalog.
Anything that does not match is "Unsorted". A line is never guessed.

Display name rule: `line` is the bare line name. When the same line name exists in more than one year
(within a category and brand), code builds the label as "<line> (<year>)"; otherwise the label is the bare name.
"""

import itertools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CATEGORY_ORDER = ["tv", "phone", "laptop", "washer", "washer_dryer", "refrigerator"]
PREFIX = {"tv": "TV", "phone": "PH", "laptop": "LT", "washer": "WS", "washer_dryer": "WD", "refrigerator": "RF"}
CATEGORY_LABEL = {"tv": "TVs", "phone": "Phones", "laptop": "Laptops", "washer": "Washers",
                  "washer_dryer": "Washer-dryers", "refrigerator": "Refrigerators"}
PRICE_UP_LINES = {("Motorola", "Moto G 5G", 2024), ("Motorola", "Moto G Power 5G", 2024)}  # rising-price case
LEADER_LINES = {("Insignia", "F50 Series QLED Fire TV", 2023), ("Toshiba", "C350 Fire TV", 2023)}  # cheapest-price anchors
DEFAULT_LINES_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog_lines.yaml"
UNSORTED_LINE = "Unsorted"
UNSORTED_LINE_ID = "unsorted"


def slug(s, n=99):
    """Upper-case alphanumerics, for SKU codes."""
    return "".join(c for c in str(s).upper() if c.isalnum())[:n]


def url_slug(text):
    """lower-case-with-dashes, for URLs."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower().replace("+", " plus ")).strip("-")


def norm(text):
    return " ".join(str(text or "").lower().split())


def parse_config(label):
    """"16GB/512GB" or "16GB/1TB" -> (ram_gb, ssd_gb)."""
    ram, ssd = label.split("/")
    ssd_gb = int(ssd[:-2]) * (1024 if ssd.endswith("TB") else 1)
    return int(ram[:-2]), ssd_gb


def storage_label(gb):
    return f"{gb // 1024}TB" if gb >= 1024 else f"{gb}GB"


def search_text(brand, model, line, variant, sku, category):
    """Normalized text a search matches against: brand, model, line, variant, sku and category label."""
    return norm(" ".join([brand, model, line, variant, sku, CATEGORY_LABEL.get(category, category)]))


def expand_catalog(lines_path=None):
    spec = yaml.safe_load(Path(lines_path or DEFAULT_LINES_PATH).read_text(encoding="utf-8"))
    targets = spec["targets"]
    years = {}
    for cat in CATEGORY_ORDER:
        for ln in spec["categories"][cat]["lines"]:
            years.setdefault((cat, ln["brand"], ln["line"]), set()).add(ln["release_year"])
    variants, seq = [], {c: 0 for c in CATEGORY_ORDER}
    for cat in CATEGORY_ORDER:
        for ln in spec["categories"][cat]["lines"]:
            dims = ln["variants"]
            repeated = len(years[(cat, ln["brand"], ln["line"])]) > 1
            label = ln["line"] + (f" ({ln['release_year']})" if repeated else "")
            names = list(dims)
            for combo in itertools.product(*[dims[n] for n in names]):
                d = dict(zip(names, combo))
                if cat == "tv":
                    size, variant, other = d["size_in"], f"{d['size_in']} inch", ()
                elif cat == "phone":
                    size, variant, other = d["storage_gb"], f"{storage_label(d['storage_gb'])} {d['color']}", (d["color"],)
                elif cat == "laptop":
                    size, variant, other = parse_config(d["config"]), f"{d['config']} {d['color']}", (d["color"],)
                else:
                    size, variant, other = d["capacity_cuft"], f"{d['capacity_cuft']:g} cu ft {d['finish']}", (d["finish"],)
                model = f"{label} {variant}"
                seq[cat] += 1
                sku = f"{PREFIX[cat]}-{slug(ln['brand'], 3)}-{seq[cat]:04d}"
                variants.append(dict(
                    category=cat, brand=ln["brand"], line=ln["line"], line_label=label, line_id=ln["id"],
                    line_slug=url_slug(f"{ln['brand']} {label}"), year=ln["release_year"], tier=ln["tier"],
                    launch=bool(ln.get("launch_family")), dims=d, size=size, other=other, variant=variant, model=model,
                    family=f"{ln['brand']} {label}", sku=sku,
                    search_text=search_text(ln["brand"], model, ln["line"], variant, sku, cat),
                    price_up=(ln["brand"], ln["line"], ln["release_year"]) in PRICE_UP_LINES,
                    leader=(ln["brand"], ln["line"], ln["release_year"]) in LEADER_LINES))
    counts = {c: sum(1 for v in variants if v["category"] == c) for c in CATEGORY_ORDER}
    if counts != targets:
        raise SystemExit(f"catalog counts {counts} do not match targets {targets}")
    if len({v["sku"] for v in variants}) != len(variants) or len({(v["brand"], v["model"]) for v in variants}) != len(variants):
        raise SystemExit("catalog has duplicate SKUs or product names")
    return variants


@dataclass(frozen=True)
class Catalog:
    variants: list
    by_name: dict

    def match(self, brand, model):
        """The catalog variant with this brand and model, or None. Exact match after trimming, case and spacing only."""
        b, m = norm(brand), norm(model)
        hit = self.by_name.get((b, m))
        if hit is None and b and m.startswith(b + " "):          # "Samsung QN90D ..." typed with the brand in front
            hit = self.by_name.get((b, m[len(b) + 1:]))
        return hit


def load_catalog(lines_path=None):
    variants = expand_catalog(lines_path)
    return Catalog(variants=variants, by_name={(norm(v["brand"]), norm(v["model"])): v for v in variants})
