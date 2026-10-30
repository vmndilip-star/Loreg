"""Write data/demo/upload_demo_200_messy.csv: the demo stock list with the column names a real shop might use (abbreviations, and
Hindi and Telugu written in Latin letters). Same rows, same three bad rows; only the header line changes. Deterministic.

    python scripts/make_messy_demo.py

Uploading it shows the column-matching step: every header is matched by code (no model needed), the owner confirms, and the
numbers come out exactly as for data/demo/upload_demo_200.csv.
"""

import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "demo" / "upload_demo_200.csv"
TARGET = ROOT / "data" / "demo" / "upload_demo_200_messy.csv"
HEADERS = {"sku": "Item Code", "brand": "Kampani", "model": "Model No", "category": "Shreni", "qty_on_hand": "Qty OH", "unit_cost": "Cost Pr",
           "sell_price": "Ammakam Dhara", "last_sale_date": "Lst Sold", "received_date": "Recd Date", "units_sold_30d": "Sold 30d",
           "units_sold_90d": "Sold 90d", "msrp": "MRP", "is_seasonal": "Seasonal?", "supplier": "Vendor", "return_window_end": "Return By",
           "map_price": "MAP"}


def messy_text() -> str:
    rows = list(csv.reader(io.StringIO(SOURCE.read_text(encoding="utf-8"))))
    rows[0] = [HEADERS[h] for h in rows[0]]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def main() -> int:
    with open(TARGET, "w", encoding="utf-8", newline="") as f:     # the same line endings on every OS
        f.write(messy_text())
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
