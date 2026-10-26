# Loreg dataset generator v2 (market-anchored)

```
python generate_dataset_v2.py --size demo --out out_demo     # 490 SKUs (200 TV, 200 phone, 50 laptop, 26 washer, 14 washer-dryer)
python generate_dataset_v2.py --size full --out out_full     # 3,000 SKUs (adds tablets, refrigerators, 2,210 accessories)
python validate_dataset.py --dir out_full --size full        # 46 checks, exit 0 only if all pass
python generate_dataset_v2.py --size demo --anchors my_prices.csv --out out_x   # override market prices
```
Needs: python 3, numpy, pandas. Deterministic: same seed -> byte-identical CSVs.

## How prices work
`market_price_reference.csv` is the market anchor table: cheapest **regular** price (`street_floor`) and cheapest **current promo**
(`promo_floor`) per category/size, each with date, evidence level and source URL. Every model's market price is derived from it
(TVs: anchor x tier x generation; others: launch MSRP x depreciation, clamped to the floor). Cost = market price in the month
the stock was bought, so stock bought high before a price drop ends up **above** today's market (`cost_above_market`).
`market_price_history.csv` has 13 monthly points per model. Floor-leader lines (Insignia F50, Toshiba C350) sit exactly on the
anchors, so the cheapest 65in is $329.99 regular / $249.99 promo.

## Evidence levels (read this)
| level | meaning | anchors |
|---|---|---|
| sourced_2026 | found in a 2026 web source (some are secondary sites) | 5 |
| sourced_older | found in a 2024-2025 source, may be stale | 5 |
| estimated | my assumption, NOT sourced | 22 |
About 97% of SKUs price off an estimated anchor. Refresh anchors before making any pricing claim (see --anchors).

## Files
skus_master.csv (upload format) | upload_demo_200.csv (3 seeded errors) | upload_defects.csv + defects_manifest.csv (every DQ code + 4 format
edge cases that must parse) | sales_events.csv (sales + returns, 12 months, incl. Black Friday 2025 spike) | market_prices.csv |
market_price_history.csv | market_price_reference.csv | ground_truth.csv (intended/expected/computed status, edge tags, taxonomy) |
summary.csv | quality_report.json

## Edge cases covered (each present at least once, enforced by check K03)
never sold (new / old / mid-age / no received date) - sold but no received date - stockout (recent sale, dead history) - sold today but
massively overstocked - sale exactly at slow / dead threshold and one day short - seasonal item past thresholds - overpriced vs market -
cost above market - viral spike - dead item inside / outside return window - extra cost lots of the same model - launch-family
stock (Samsung 2024 TVs) - price-up trend (Moto G 2026 pattern) - customer returns in sales events - DQ defects: missing required,
missing cost, negative qty, duplicate SKU, future date, price below cost, bad category, sale before received, velocity outlier,
MSRP mismatch, zero qty - formats: MM/DD/YYYY, "$1,299.00", category synonym, "5.0" qty.

## Honest limits
- Brand/model names are real product lines used for a prototype; prices, costs, quantities and sales are synthetic.
- Appliance "models" are product lines, not retail model numbers. Phone/laptop release years are approximate.
- Thresholds, depreciation curves, cost ratios and the outlier floor (30 units/30 days) are tunable assumptions.
- The passing checks prove structure and internal consistency, not that a price is true. Truth depends on the anchors.
- Hackathon rule: only code built during the event may be shown. Commit this into your repo after kickoff and say so in the README.
