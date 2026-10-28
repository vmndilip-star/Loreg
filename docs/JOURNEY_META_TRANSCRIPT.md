# Journey on the Meta backend: transcript and variance

Recorded 2026-10-27 with `python scripts/journey_meta.py` (real Meta Model API, model `muse-spark-1.3`, reasoning effort `minimal`).
Data is the bundled **synthetic** demo stock list; the database is thrown away after each run. Every number in the replies was
computed by code; the model only wrote words. This file was passed through a redaction step (key-like text, the key itself and
local paths are removed) and contains no secret.

## Three runs on the final code

| run | checks | daily brief (s) | chat before help (s) | chat after help (s) | TV-SAM-0040 | TV-SAM-0060 | TV-SAM-0032 (MAP-blocked) | same as the rules |
|---|---|---|---|---|---|---|---|---|
| 1 | 19/19 | 4.8 | 4.2 | 2.2 | discount | return | bundle | 3/3 |
| 2 | 19/19 | 3.8 | 3.3 | 2.1 | discount | return | bundle | 3/3 |
| 3 | 19/19 | 2.5 | 5.0 | 2.8 | bundle | return | bundle | 2/3 |

- **Failures: none.** 57 of 57 checks passed. In every run the model's answer was used (no deterministic fallback): the router, three inventory explanations, three recommendations and the daily brief.
- **Variance in the options:** the first item is a discount in two runs and a bundle in one. Both are inside the allowed set, and the rules would propose the discount. The return and the MAP-blocked item were the same in all three runs; no run offered a discount on the MAP-blocked item.
- **Variance in wording and time:** reasons are worded differently each run. Times: daily brief 2.5 to 4.8 s, first chat answer 3.3 to 5.0 s, plan 2.1 to 2.8 s.
- **Three earlier runs** on the code just before the last fixes also passed 57 of 57 checks, with the same kind of variance in the first item (a 15% discount twice, a bundle once). They exposed a display bug in the web page (a discount card read "Discount 5% 5%"), now fixed and covered by the browser test.
- **Not covered:** these runs use one fixed stock list and one fixed set of questions. They show that the checks hold, not how often the model will phrase something the grounding check rejects on other data.

## Transcript of run 1

# Journey on the Meta backend, 2026-10-27 22:00
Synthetic demo stock list, throwaway database. Model output below is real; every number is computed by code.

## 1. Upload (demo file, same path as a real upload)
rows 200, kept 197, blocked 3; money at risk 195,939.39 USD

## 2. Daily brief
(4.8 s) source=model

> Slow and dead stock ties up capital and needs review.
> - Samsung TVs alert has no dated source attached.
> - Launch-family items that are dead or slow include 7 items.
> - Money at risk with no plan yet is $195,939, including $99,011 in dead stock in 29 items and $96,928 in slow stock in 19 items.
- [PASS] daily brief written by the model, not the fallback: source=model

## 3. Chat before help (alert first)
(4.2 s) agents: router:model:1281ms, inventory:model:2109ms, inventory:model:2900ms, inventory:model:2892ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. No dated news source is attached. These items are in the catalog's launch family (a newer generation is expected); no launch date is claimed. Want me to plan what to do with them?

cards: ['alert', 'item', 'item', 'item']
- [PASS] first event is a status
- [PASS] alert card then item cards, and no plan yet
- [PASS] reply ends with the question
- [PASS] reply is the alert sentences only; item detail is in the cards
- [PASS] inventory explanations used the model: router:model:1281ms, inventory:model:2109ms, inventory:model:2900ms, inventory:model:2892ms
- [PASS] every item card is a real SKU in stock

## 4. Help, then the plan in the chat
(2.2 s) agents: router:model:0ms:cached, inventory:model:1ms:cached, inventory:model:1ms:cached, inventory:model:0ms:cached, recommender:model:2160ms, recommender:model:2163ms, recommender:model:2165ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. No dated news source is attached. These items are in the catalog's launch family (a newer generation is expected); no launch date is claimed. Here is one option for each item. You decide: nothing changes until you accept.
- TV-SAM-0040 Samsung QN85D Neo QLED 65 inch: **Discount 15%**; reasons: Item is slow stock; 150 days of stock at current pace; 15% is highest discount within rules
- TV-SAM-0060 Samsung Q60D QLED 85 inch: **Return**; reasons: Slow stock with 180 days of stock; Return window open until 2026-11-25 can free $6,690 tied up
- TV-SAM-0032 Samsung QN90D Neo QLED 43 inch: **Bundle**; reasons: Slow stock with 540 days of stock suggests bundling to move it; Bundling is allowed while a discount is blocked by MAP; Supplier return window is closed so return is not available
- [PASS] one option per item, all in the allowed set
- [PASS] recommender used the model: router:model:0ms:cached, inventory:model:1ms:cached, inventory:model:1ms:cached, inventory:model:0ms:cached, recommender:model:2160ms, recommender:model:2163ms, recommender:model:2165ms
- [PASS] no discount is offered on the item whose discount would go below its minimum advertised price
- [PASS] every plan card carries its citations (numbers were checked by code before display)

## 5. Accept, then undo
- [PASS] accept lowers money at risk by that item's tied-up money: 19593939 -> 18529049
- [PASS] undo restores it

## 6. A question about one item
(0.6 s) agents: router:model:601ms, inventory:model:0ms:cached

> You have 10 units on the shelf tying up $10,649. It is slow stock with 19 days since the last sale and about 150 days of stock at the current pace, and the supplier return window is closed or none.
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

> Not enough data: I don't have a price or a date for that. Loreg only knows your own stock numbers and the dated news it has stored, so it cannot say what a product will cost, when it will ship or what a rival charges. What is stored: Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. No dated news source is attached. These items are in the catalog's launch family (a newer generation is expected); no launch date is claimed. Want me to plan what to do with them?
- [PASS] an unknowable question gets 'not enough data' without a model call
- [PASS] it cites only stored figures and invents no date

## Result: 19 of 19 checks passed
