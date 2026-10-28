# Journey on the Meta backend, final demo data with the required pace

Run on 2026-10-28 with `python scripts/journey_meta.py` against the code at commit `73ddadc` (after the synthetic launch date and the required pace were added).
Real Meta Model API, model `muse-spark-1.3`, reasoning effort `minimal`. The data is the bundled **synthetic** demo stock list, in demo mode,
so the alert's source is the labelled synthetic article (now with a made-up launch date) and the news list holds the real items captured on 2026-10-28 as "Stored copy".
Every number in the replies was computed by code; the model only wrote words. The transcript below was passed through the redaction step
(key-like text, the key itself and local paths are removed) and contains no secret.

## Result

I ran the journey three times on this code.

- **Run 1: 23 of 24 checks.** One failure: the daily brief fell back to the rules text (`source=fallback`). The fallback is the safety net working as designed
  (the model's text failed a check, or the model did not answer); I did not capture which, so I cannot say whether the new launch date in the alert fact played a part.
  Every other check passed, including the new pace check.
- **Runs 2 and 3: 24 of 24 checks.** The transcript below is run 3. `fallback_agent_calls` is empty: the router, three inventory explanations, three recommendations
  and the daily brief all used the model's answer.
- **Timings (run 3):** daily brief 3.5 s; first chat answer 3.6 s; plan in the chat after help 2.0 s. The date, price, injection and invented-SKU answers come from code, with no model call.
- **Plan (run 3):** TV-SAM-0040 discount 15%, TV-SAM-0060 return, TV-SAM-0032 bundle (a discount there would go below the minimum advertised price, and none was offered).
  All three agree with what the rules would allow; each answer was checked against the rules before it was stored.
- **New in this run:** the alert states the synthetic launch date (November 20, 2026, 25 days after the stock list's date) and the required pace
  (28 units, 23 days from the app's clock, at least 2 units a day), worded as "a required pace, not a sales forecast" and "from the synthetic demo article".
  The date question is answered "From your stored news only" and says the source is synthetic.
- **Not shown by this run:** a live news scan (robots.txt on the Samsung site often times out), a real-endpoint test of `openai_compat`, and any open-weights model.

## Transcript

# Journey on the Meta backend, 2026-10-28 03:30
Synthetic demo stock list, throwaway database. Model output below is real; every number is computed by code.

## 1. Upload (demo file, same path as a real upload)
rows 200, kept 197, blocked 3; money at risk 195,939.39 USD
alert source: 'Demo article (synthetic)' (demo_synthetic), published 2026-10-26, fetched 2026-10-28; news list: 11 items (['Demo article (synthetic)', 'Stored copy'])
- [PASS] the demo alert carries a labelled synthetic source with published and fetched dates
- [PASS] the news list shows the real items as 'Stored copy' with their true fetch date

## 2. Daily brief
(3.5 s) source=model

> Launch alert fronts high capital tied in slow and dead stock.
> - Samsung TVs alert for Loreg demo is active.
> - Money at risk with no plan yet totals $195,939.
> - Money tied up in dead stock is $99,011 in 29 items.
> - Money tied up in slow stock is $96,928 in 19 items with 7 launch-family items that are dead or slow.
- [PASS] daily brief written by the model, not the fallback: source=model

## 3. Chat before help (alert first)
(3.6 s) agents: router:model:1021ms, inventory:model:1486ms, inventory:model:2601ms, inventory:model:2592ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Launch date: November 20, 2026 (as written in the article). To sell all 28 units before the launch on November 20, 2026 (23 days from today), you would need to sell at least 2 units a day. It is a required pace, not a sales forecast. The launch date comes from the synthetic demo article. Want me to plan what to do with them?

cards: ['alert', 'item', 'item', 'item']
- [PASS] first event is a status
- [PASS] alert card then item cards, and no plan yet
- [PASS] reply ends with the question
- [PASS] reply is the alert sentences only; item detail is in the cards
- [PASS] the alert carries the required pace to the synthetic launch date, worded as not a forecast, computed by code: 28 units, 23 days, 2 a day
- [PASS] inventory explanations used the model: router:model:1021ms, inventory:model:1486ms, inventory:model:2601ms, inventory:model:2592ms
- [PASS] every item card is a real SKU in stock

## 4. Help, then the plan in the chat
(2.0 s) agents: router:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, recommender:model:1859ms, recommender:model:1861ms, recommender:model:2007ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Launch date: November 20, 2026 (as written in the article). To sell all 28 units before the launch on November 20, 2026 (23 days from today), you would need to sell at least 2 units a day. It is a required pace, not a sales forecast. The launch date comes from the synthetic demo article. Here is one option for each item. You decide: nothing changes until you accept.
- TV-SAM-0040 Samsung QN85D Neo QLED 65 inch: **Discount 15%**; reasons: Slow stock with high days of cover; Discount up to allowed limit can accelerate sales
- TV-SAM-0060 Samsung Q60D QLED 85 inch: **Return**; reasons: Supplier return window open until 2026-11-25; 4 units tie up $6,690 with 180 days of stock; No sale for 72 days and slow stock status
- TV-SAM-0032 Samsung QN90D Neo QLED 43 inch: **Bundle**; reasons: 540 days of stock with 83 days since last sale; Discount is blocked and return window is closed; Bundling is allowed
- [PASS] one option per item, all in the allowed set
- [PASS] recommender used the model: router:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, recommender:model:1859ms, recommender:model:1861ms, recommender:model:2007ms
- [PASS] no discount is offered on the item whose discount would go below its minimum advertised price
- [PASS] every plan card carries its citations (numbers were checked by code before display)

## 5. Accept, then undo
- [PASS] accept lowers money at risk by that item's tied-up money: 19593939 -> 18529049
- [PASS] undo restores it

## 6. A question about one item
(1.9 s) agents: router:model:1847ms, inventory:model:0ms:cached

> You have 10 units on the shelf tying up $10,649. This is slow stock with 150 days of stock at the current pace, and it has been 19 days since the last sale.
- [PASS] answered with an item card for that SKU

## 7. Things it must not do
- [PASS] an invented SKU is rejected: HTTP 404
invented SKU in the text -> (0.0 s)

> [error event] I can't find an item with SKU TV-ZZZ-9999 in your stock.
- [PASS] an invented SKU in the text is not treated as an item
injection -> (0.0 s) agents: none

> I can help with three things: why an item is stuck (give me its SKU, for example TV-SAM-0042), what a newer launch could mean for your stock, or today's summary. What would you like to know?
- [PASS] injection-like text never reaches a model and plans nothing
question the data cannot answer -> (0.0 s) agents: none

> Not enough data: I don't have a price or a date for that. Loreg only knows your own stock numbers and the dated news it has stored, so it cannot say what a product will cost, when it will ship or what a rival charges. This source is a synthetic demo article, not real news. What is stored: Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Launch date: November 20, 2026 (as written in the article). To sell all 28 units before the launch on November 20, 2026 (23 days from today), you would need to sell at least 2 units a day. It is a required pace, not a sales forecast. The launch date comes from the synthetic demo article. Want me to plan what to do with them?
- [PASS] an unknowable question gets 'not enough data' without a model call
- [PASS] it cites only stored figures and invents no date
date question on the synthetic article -> (0.1 s) agents: none

> From your stored news only: This source is a synthetic demo article, not real news. Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Launch date: November 20, 2026 (as written in the article). To sell all 28 units before the launch on November 20, 2026 (23 days from today), you would need to sell at least 2 units a day. It is a required pace, not a sales forecast. The launch date comes from the synthetic demo article. I have no other source for launch dates. Want me to plan what to do with them?
- [PASS] a date question is answered 'From your stored news only' and says the source is a synthetic demo article, without a model call
- [PASS] it gives only the launch date the stored synthetic article states, and no other year

## Result: 24 of 24 checks passed
