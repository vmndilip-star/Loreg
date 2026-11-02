# Evals

Run on 2026-10-30 from branch `submission-hardening`. Raw results are in `evals/results_*.json` and `evals/fallback_*.json`.
Re-run with:

```
python scripts/run_evals.py --backend mock            # or --backend meta (needs META_API_KEY in .env)
python scripts/eval_fallback_rate.py --backend meta --items 5 --runs 3
```

## 1. Eval set: rules and recommender (`evals/cases.yaml`, 21 cases)

Each case is one stocked item, with the expected status and allowed options **worked out by hand** from
`config/thresholds.yaml`. A case passes when:

1. the rules (`app/core/classify.py`, `app/core/actions.py`) give exactly the expected status, allowed options, largest
   discount, below-cost flag and MAP flag; and
2. the recommender's proposal on that backend is one of the allowed options, with a discount from the allowed steps, still
   "proposed".

The cases cover a below-cost trap, a return window that is open, closed, or closing today, a MAP that caps or blocks the
discount, deep-dead stock where a small below-cost discount is allowed (with and without a MAP), near-zero data, tiny
capital, conflicting signals (a recent sale but 1,200 days of stock), a seasonal item, zero margin, an exact 20% margin, and
items that are not at risk.

| Backend | Cases passed | Recommender fallback rate | Latency per call |
|---|---|---|---|
| mock | 21 of 21 | n/a: no model is asked; proposals are the rules' own | under 1 ms |
| meta (`muse-spark-1.3`, reasoning effort `minimal`) | 21 of 21 | 0 of 21 | 1.0 s to 3.7 s |

What the Meta model proposed:

| Case | Allowed | Proposal |
|---|---|---|
| dead-healthy-margin | discount up to 20%, bundle, hold | bundle |
| below-cost-trap | bundle, hold | bundle |
| return-window-open | return, bundle, hold | return |
| return-window-expired | bundle, hold | bundle |
| return-window-closes-today | return, bundle, hold | return |
| map-caps-discount | discount 5%, bundle, hold | discount 5% |
| map-blocks-discount | bundle, hold | bundle |
| deep-dead-below-cost | discount up to 10% (below cost), bundle, hold | bundle |
| deep-dead-below-cost-but-map | bundle, hold | bundle |
| near-zero-data | discount up to 20%, bundle, hold | hold |
| tiny-capital | discount up to 20%, bundle, hold | bundle |
| conflicting-signals | discount up to 20%, bundle, hold | bundle |
| healthy-item | discount up to 20%, hold | hold |
| seasonal-washer | discount up to 20%, bundle, hold | discount 20% |
| zero-margin-fridge | bundle, hold | bundle |
| zero-margin-fridge-very-old | discount up to 10% (below cost), bundle, hold | bundle |
| exactly-twenty-percent-margin | discount up to 20%, bundle, hold | discount 20% |
| phone-everything-allowed | return, discount up to 10%, bundle, hold | return |
| nothing-on-the-shelf | discount up to 20%, bundle, hold | hold |
| new-stock | discount up to 20%, bundle, hold | bundle |
| price-below-cost | bundle, hold | bundle |

## 2. Fallback rate over the demo workload (`scripts/eval_fallback_rate.py`)

One run includes:
- the demo upload and Loreg Daily;
- the three suggested questions and "Why is SKU stuck?" for the top 5 items;
- help, then a plan for up to 10 items of the first alert.

Latency leaves out answers served from the cache.

| Backend | Runs | Agent calls | model_ok | retried_ok | fallback | Fallback rate | p50 | p95 |
|---|---|---|---|---|---|---|---|---|
| meta | 3 | 75 | 75 | 0 | 0 | 0.0% | 1,969 ms | 3,133 ms |
| mock | 1 | 25 | 0 | 0 | 0 | n/a (25 mock) | 0 ms | 5 ms |

Per agent on meta (3 runs):
- **Router:** 24 calls, p50 938 ms.
- **Inventory:** 24 calls, p50 1,989 ms.
- **Recommender:** 21 calls, p50 2,891 ms.
- **Daily:** 6 calls, p50 4,500 ms.

None fell back.

## What these numbers do and do not show

- **A pass is a safety check, not a quality check.** It means the proposal stayed inside what the rules allow and passed the
  grounding checks. It does not judge whether that option was the best one for the shop. For example, on
  `dead-healthy-margin` the model chose "bundle" where a discount was also allowed. Both pass.
- **Mock shows the rules are right, not the model.** On mock the recommender returns the rules' own choice. Its 21 of 21
  shows the hand-worked expectations match the code.
- **The sample is small.** A 0% fallback rate over 75 calls on one day does not mean the model never fails. The app still
  falls back to the rules whenever an answer fails a check, and the header badge and `/api/quality` report how often that
  happens in use.
- **Two parts of Loreg Watch are not covered here.**
  - The Market agent reads live news only when "Check for news" runs, so it is not in this workload. Its injection and
    date checks are covered offline by `tests/test_watch*.py`.
  - The open-weights backend (`openai_compat`) was not run; it is tested only against fakes.
