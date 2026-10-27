# Loreg product database

`catalog_lines.yaml` lists 507 real product lines. `generate_catalog.py` expands them into exactly **3,000 unique products**
(tv 800, phone 800, laptop 450, washer 300, washer-dryer 150, refrigerator 500) and simulates a shop's stock and a year of sales.
`validate_catalog.py` checks the result. Nothing in `data/generated/` is committed; it is rebuilt from the seed.

```
python data/generate_catalog.py                          # writes data/generated/ (seed 2026, as of 2026-10-26)
python data/generate_catalog.py --seed 7 --out out7      # another database; the same arguments give byte-identical files
python data/generate_catalog.py --anchors my_prices.csv  # refresh market prices from a CSV (same columns as market_anchors.csv)
python data/validate_catalog.py --dir data/generated     # 50+ checks, exit 0 only if all pass
python data/validate_catalog.py --seeds 1-10             # generate and validate ten seeds
```

## What is real and what is not

- **Names are real product lines, but unverified.** They were written from memory and I could not check them against a
  catalogue. Every line carries `name_verified: false` and a `name_confidence` (high, medium or low).
  **`names_to_verify.csv` lists every line so you can check them.** Its `priority` column is `high` for the launch family, every
  line that appears in `upload_demo_200.csv`, and the lines of the 40 biggest at-risk items, so check those first.
- **Prices, costs, quantities and sales are synthetic.** No price here is a real price. Nothing is invented as a model number:
  appliances use descriptive line names (brand + type), not codes.
- **Market prices come from an anchor table**, `market_anchors.csv`: the cheapest regular price and the cheapest current promo price
  per category and size (TV sizes) or segment (everything else). Each product's market price is derived from its anchor, so a bigger
  size of the same line never costs less. Cost is the market price in the month the stock was bought, so some stock sits above
  today's market (about 1 SKU in 5; 20.7% in the default database).

### How many anchors are sourced

| evidence | anchors | meaning |
|---|---|---|
| `sourced_2026` | 5 | found in a 2026 web source (some are secondary sites) |
| `sourced_older` | 5 | found in a 2024 to 2025 source; may be stale |
| `estimated` | 36 | **my assumption, not sourced**; 16 of these are new rows added beyond the prior reference table |

About 12.5% of SKUs price off a sourced anchor and about 87.5% off an estimated one. Treat every dollar figure as an illustration.
Refresh the anchors before making any pricing claim. The TV anchors for 65 inch ($329.99 regular, $249.99 promo) are the best
supported; the Insignia F50 and Toshiba C350 lines sit exactly on the anchors and are the cheapest at each anchored size.

## The shop

- **Status mix** per category: about 13% dead, 13% slow, 6% new, the rest healthy. Thresholds live in `config/thresholds.yaml`
  (assumptions, not facts). The classifier and the 11 data-quality codes are in `app/core`.
- **Fixture rows** (35, about 1% of SKUs) are hand-labelled edge cases, each capped at about $3,000 of stock so they do not move the
  headline numbers: never sold (new, old, mid-age, no received date), sold with no received date, stockout, sold today but
  overstocked, a sale exactly at and one day short of the slow and dead thresholds, seasonal past the thresholds, overpriced against
  the market, cost above market, a viral spike, dead stock inside and outside its return window, launch-family stock and a price-up phone.
- **Launch family:** the Samsung 2024 TV lines QN90D, QN85D, Q80D, Q70D, Q60D and S90D (33 products), weighted toward dead and slow.
- **Price-up case:** Moto G 5G (2024) and Moto G Power 5G (2024) have a rising market price.

## Files

| file | contents |
|---|---|
| `skus_master.csv` | the 3,000 products in Loreg upload format |
| `sales_events.csv` | 12 months of sales and customer returns |
| `market_prices.csv`, `market_price_history.csv` | market price today and 13 monthly points per SKU, with the anchor and its evidence label |
| `ground_truth.csv` | intended, expected and computed status; edge tags; line; launch family; fixture flag |
| `summary.csv` | stock at cost, dead and slow capital per category, with and without fixture rows |
| `upload_demo_200.csv` | 200 rows from the 3,000 with exactly 3 errors (missing cost, negative quantity, duplicate SKU) |
| `upload_defects.csv`, `defects_manifest.csv` | one row per data-quality code plus 4 format cases that must parse |

## Limits

- The validator proves structure and internal consistency (counts, ordering, anchors, labels, determinism), not that any price is true.
- The status mix is by count; the share of **money** differs because quantities differ (see `summary.csv`).
- Thresholds, depreciation, cost ratios and the outlier floor (30 units in 30 days) are tunable assumptions.
