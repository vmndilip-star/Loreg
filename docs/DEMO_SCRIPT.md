# Demo script (3 minutes)

The figures below come from the demo stock list with the stock date fixed at 2026-10-26, as checked by
`scripts/demo_smoke.py` on 2026-10-30 (15 of 15 checks passed on mock and on Meta).

On Meta, the wording of the brief, the explanations and the chosen options changes from run to run. The money figures never
change, because code computes them.

## Before you start (5 minutes before)

1. Stop the server if it is running. Then reset to a known state:
   `python scripts/reset_demo.py --yes`
2. Run the smoke test on the backend you will demo with:
   `python scripts/demo_smoke.py --backend meta`
   It must print `15 of 15 checks passed`. If it fails on Meta, demo on mock (see Plan B).
3. Check `.env`: `LLM_BACKEND=meta`, `LOREG_DEMO=1`, `LOREG_AS_OF=2026-10-26`.
4. Start the server: `python -m uvicorn app.main:app`, then open http://127.0.0.1:8000/. You are signed in to the demo shop.
5. Check that the badge under the header says **Backend: meta**.
6. Do **not** press "Check for news" during the demo. The alert already has its labelled source.

The reset already loaded the stock list. Show the Upload step anyway: "Use the demo stock list" loads the same file again and
gives the same numbers.

## The script

| Time | Click or show | Say |
|---|---|---|
| 0:00 | Home screen | "Small electronics shops lose money on stock that stopped selling, and every new launch makes it worse. Loreg finds that stock and tells the owner before the loss grows." |
| 0:15 | **Upload** → **Use the demo stock list** | "This is the shop's export: 200 rows. Loreg checks every row before counting anything." Point at **197 rows OK, 3 blocked**: a missing cost, a negative quantity, a duplicate SKU. "Bad rows are set aside, not guessed." |
| 0:40 | **Home** | Point at **Money at risk $195,939**, then **29 dead** and **19 slow**. "Every number here is computed by plain code. The model never does the math." |
| 0:55 | Loreg Daily → **Write today's summary** | "The morning brief. The model writes the sentences, but each one cites facts the code computed. A sentence with a number that is in no fact is rejected." Point at the line saying who wrote it. |
| 1:15 | The **Alert** tile | "A launch alert: 7 Samsung TVs are dead or slow, with $30,989 tied up, and a newer line is coming." Point at the source: **Demo article (synthetic)**, its published and fetched dates, the launch date as written in the article, and the required pace. "For the demo this article is synthetic, and it says so. Real news is fetched only from an allowlisted site and treated as untrusted data." |
| 1:35 | **Ask** → **What should I clear before the launch?** | "Ask Loreg streams its steps: understood the question, read the stored news, read the numbers." "It alerts first and asks before planning anything." |
| 1:55 | **Yes, plan it** | "One option per item: discount, bundle, return or hold. Code decides which options are allowed; the model picks one and explains it." Point at "Chosen by the model from the allowed options, and checked by code" on a card. |
| 2:20 | **Accept** on the first card (TV-SAM-0040) | Point at **Money at risk**: it drops by exactly that item's tied-up capital (the card says **$10,649 tied up**; the screens round to whole dollars). "That money is now an action in progress. Loreg does not call it freed until the units sell." |
| 2:40 | The badge under the header | "Backend: meta, and how many items used the rules-based fallback. When the model is slow or wrong, the rules answer, and the screen says so." |
| 2:50 | Close | "21 of 21 eval cases pass on the real model, and 0 of 75 calls fell back on today's run. The limits are written down in the README. The owner makes every decision." |

**Optional, if you have 20 seconds more:** on Upload, choose `data/demo/upload_demo_200_messy.csv`. It has headers like
"Qty OH", "Cost Pr" and "Ammakam Dhara". Loreg matches every column, shows how it matched each one, and stores nothing
until you press **Confirm**.

## Plan B for every live dependency

| If this fails | What you see | Do this |
|---|---|---|
| **The Meta API is slow or down** | Steps say "(by rules)". Plan cards say "Using rules-based fallback". The badge counts them. | Keep going: the numbers are identical and the labels are honest. Say "the model timed out, so the rules answered; that is the design". If the API is completely unreachable, restart with `LLM_BACKEND=mock`; the badge then says "offline test writer, no model". |
| **The streamed answer stalls** (a proxy, flaky Wi-Fi) | Nothing appears under the question. | The page asks again without streaming once, automatically. If it still fails, reload the page and ask again. |
| **News site or `robots.txt` unreachable** | Not visible, because you do not press "Check for news". | The demo alert uses its own labelled synthetic source, so nothing live is needed. If someone asks to see live news, open Watch: stored copies are listed with their true dates. |
| **The numbers look wrong, or the state is messy** | A different money figure, or plans already accepted | Stop the server, run `python scripts/reset_demo.py --yes`, start it again (about 15 seconds). |
| **The laptop or server will not start** | No page | Show the recorded run, or `python scripts/demo_smoke.py` output, which walks the same path headless. |
| **No network at all** | The badge says the backend is meta, and every step falls back | Switch to `LLM_BACKEND=mock` and restart. Everything except the model's wording works offline. |
