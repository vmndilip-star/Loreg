"""Map a shop's own column headers to Loreg's stock-list fields. Pure functions: no I/O, no model.

Matching runs in this order, and a model is asked only about what is left (app/agents/columns.py):
  1. exact: the header is the field name ("qty_on_hand"), in any case, with spaces, dashes or dots for underscores;
  2. alias: a known way of writing it, including abbreviations ("Qty OH", "Cost Pr", "Lst Sold") and Hindi and Telugu written in
     Latin letters ("kharid keemat", "konugolu dhara").
Whatever proposes a mapping, `validate` decides whether it can be used: every target is a known field, no field is used twice,
every required field is covered, and the sample values of each mapped column read as that field's type. The owner confirms the
mapping before anything is stored, and the data-quality checks (app/core/quality.py) still run on every row afterwards.
"""

import re

from app.core.quality import parse_date, parse_int, parse_money

FIELDS = ("sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "last_sale_date", "received_date",
          "units_sold_30d", "units_sold_90d", "msrp", "is_seasonal", "supplier", "return_window_end", "map_price")
REQUIRED = ("sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price")
LABELS = {"sku": "SKU (item code)", "brand": "Brand", "model": "Model", "category": "Category", "qty_on_hand": "Quantity on the shelf",
          "unit_cost": "Unit cost (what you paid)", "sell_price": "Selling price", "last_sale_date": "Last sale date",
          "received_date": "Received date", "units_sold_30d": "Units sold, last 30 days", "units_sold_90d": "Units sold, last 90 days",
          "msrp": "List price (MSRP)", "is_seasonal": "Seasonal (1 or 0)", "supplier": "Supplier", "return_window_end": "Return window ends",
          "map_price": "Minimum advertised price"}
KIND = {"qty_on_hand": "int", "units_sold_30d": "int", "units_sold_90d": "int", "unit_cost": "money", "sell_price": "money", "msrp": "money",
        "map_price": "money", "last_sale_date": "date", "received_date": "date", "return_window_end": "date", "is_seasonal": "flag"}

# Normalised aliases (see norm). Transliterations are common spellings, not a dictionary: an unknown spelling goes to the model,
# and the owner confirms whatever is proposed.
ALIASES = {
    "sku": {"sku code", "item code", "item id", "item no", "product code", "product id", "code", "barcode", "article no", "part no",
            "saman code", "maal code", "vastu code", "sarukulu code", "vastuvu code"},
    "brand": {"brand name", "make", "company", "manufacturer", "mfr", "kampani", "company naam", "brand naam", "tayaridaru"},
    "model": {"model no", "model number", "model name", "item", "item name", "product", "product name", "description", "desc",
              "naam", "saman", "saman ka naam", "vastu", "vastuvu", "vastuvu peru", "peru", "model peru"},
    "category": {"cat", "dept", "department", "type", "product type", "group", "shreni", "shrenee", "varg", "prakar", "vargam", "rakam"},
    "qty_on_hand": {"qty", "qty oh", "qty on hand", "on hand", "oh", "stock", "stock qty", "in stock", "units", "quantity", "balance qty",
                    "closing stock", "maal", "matra", "sankhya", "stock matra", "dukaan mein", "nilva", "sarukulu", "sankhya nilva", "parimanam"},
    "unit_cost": {"cost", "cost pr", "cost price", "unit cost", "purchase price", "purchase rate", "buy price", "landed cost", "cp", "paid",
                  "lagat", "kharid", "kharid mulya", "kharid keemat", "kharid kimat", "khareed daam", "konugolu dhara", "konugolu vela", "kharchu"},
    "sell_price": {"price", "sell", "sell pr", "selling price", "sale price", "retail price", "tag price", "sp", "rate", "mrp rate",
                   "bikri mulya", "bikri keemat", "vikray mulya", "daam", "keemat", "kimat", "ammakam dhara", "ammakam vela", "dhara", "vela"},
    "last_sale_date": {"last sale", "last sold", "lst sold", "lst sale", "last sold date", "last sale dt", "date last sold", "last sold on",
                       "aakhri bikri", "akhri bikri", "antim bikri", "chivari ammakam", "chivari ammakam tedi"},
    "received_date": {"received", "recd", "recd date", "rcvd", "rcvd date", "date received", "grn date", "inward date", "receipt date",
                      "aaya", "prapti tithi", "vachina tedi", "andina tedi"},
    "units_sold_30d": {"sold 30d", "sold 30 days", "sales 30d", "units 30d", "30d sales", "30 day sales", "last 30 days", "bikri 30 din",
                       "30 rojula ammakam"},
    "units_sold_90d": {"sold 90d", "sold 90 days", "sales 90d", "units 90d", "90d sales", "90 day sales", "last 90 days", "bikri 90 din",
                       "90 rojula ammakam"},
    "msrp": {"list price", "mrp", "rrp", "msrp price", "max retail price", "adhiktam khudra mulya"},
    "is_seasonal": {"seasonal", "seasonal?", "season", "is seasonal", "mausami", "mausam", "kalanugunam"},
    "supplier": {"vendor", "distributor", "supplier name", "vendor name", "dealer", "aapurtikarta", "sarafaradaru", "vyapari"},
    "return_window_end": {"return by", "return till", "return until", "return deadline", "rtv date", "return date", "wapsi tak", "vaapasi tedi"},
    "map_price": {"map", "min adv price", "minimum advertised price", "map price", "min advertised"},
}
_ALIAS_TO_FIELD = {a: f for f, names in ALIASES.items() for a in names}


def norm(header: str) -> str:
    """'Qty_OH ' -> 'qty oh'; 'Cost Pr.' -> 'cost pr'; 'Seasonal?' keeps its question mark."""
    h = re.sub(r"[_\-./#():$]+", " ", str(header or "").strip().lower())
    return re.sub(r"\s+", " ", h).strip()


def match_known(headers: list[str]) -> tuple[dict, dict]:
    """(mapping header -> field, how header -> "exact" | "alias") for the headers code can place. A field is used once: the first
    header that matches it wins. Headers code cannot place are left out (for the model, then the owner)."""
    mapping, how, used = {}, {}, set()
    for kind in ("exact", "alias"):
        for h in headers:
            if h in mapping:
                continue
            n = norm(h)
            field = (n.replace(" ", "_") if n.replace(" ", "_") in FIELDS else None) if kind == "exact" else _ALIAS_TO_FIELD.get(n)
            if field and field not in used:
                mapping[h], how[h], used = field, kind, used | {field}
    return mapping, how


def _reads_as(kind: str, value: str) -> bool:
    if kind == "int":
        return parse_int(value) is not None
    if kind == "money":
        return parse_money(value) is not None
    if kind == "date":
        return not isinstance(parse_date(value), str) and parse_date(value) is not None
    if kind == "flag":
        return value.strip().lower() in ("0", "1", "true", "false", "yes", "no", "y", "n")
    return True


def validate(mapping: dict, headers: list[str], sample_rows: list[dict], require_all: bool = True) -> list[str]:
    """Problems with a proposed mapping (header -> field); empty means it can be shown to the owner as complete.
    require_all=False skips the required-fields check (used to judge a partial suggestion the owner will complete)."""
    problems = []
    unknown_headers = [h for h in mapping if h not in headers]
    if unknown_headers:
        problems.append(f"names columns the file does not have: {unknown_headers}")
    bad = {h: f for h, f in mapping.items() if f not in FIELDS}
    if bad:
        problems.append(f"uses fields Loreg does not have: {sorted(set(bad.values()))}")
    targets = [f for f in mapping.values() if f in FIELDS]
    twice = sorted({f for f in targets if targets.count(f) > 1})
    if twice:
        problems.append(f"uses a field for more than one column: {twice}")
    missing = [f for f in REQUIRED if f not in targets]
    if missing and require_all:
        problems.append(f"leaves required fields without a column: {missing}")
    for h, f in mapping.items():
        kind = KIND.get(f)
        if not kind or h not in headers:
            continue
        values = [str(r.get(h) or "").strip() for r in sample_rows]
        values = [v for v in values if v]
        if values and sum(_reads_as(kind, v) for v in values) * 2 < len(values):      # most sample values must read as the type
            problems.append(f"column {h!r} does not look like {LABELS[f].lower()} (sample: {values[0][:30]!r})")
    return problems


def apply(rows: list[dict], mapping: dict) -> list[dict]:
    """Rows with Loreg's field names. Columns not in the mapping are dropped."""
    return [{field: r.get(h) for h, field in mapping.items()} for r in rows]
