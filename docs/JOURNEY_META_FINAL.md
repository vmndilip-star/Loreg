# Journey on the Meta backend, final demo data

Run on 2026-10-28 with `python scripts/journey_meta.py` against the code at commit `04ae8f5` (plus the script change committed with this file).
Real Meta Model API, model `muse-spark-1.3`, reasoning effort `minimal`. The data is the bundled **synthetic** demo stock list, in demo mode,
so the alert's source is the labelled synthetic article and the news list holds the real items captured on 2026-10-28 as "Stored copy".
Every number in the replies was computed by code; the model only wrote words. The text below was passed through the redaction step
(key-like text, the key itself and local paths are removed) and contains no secret.

## Result

- **Checks: 23 of 23 passed.** No failures.
- **Fallback use: none.** Every model step used the model's answer: the router, three inventory explanations, three recommendations and the daily brief
  (`fallback_agent_calls` is empty). The date, price, injection and invented-SKU questions never reach a model by design.
- **Timings:** daily brief 4.4 s; first chat answer (router plus three parallel inventory calls) 5.8 s;
  plan in the chat after help (three parallel recommender calls) 2.1 s; one-item question 1.5 s; the unknown-fact, date,
  injection and invented-SKU answers 0.0 to 0.1 s, because code answers them.
- **Plan:** TV-SAM-0040 bundle, TV-SAM-0060 return, TV-SAM-0032 bundle (a discount there would go below the minimum advertised price, and none was offered).
  For the first item the rules would propose a 5% discount; the model chose a bundle, which is inside the allowed set, so it was stored.
- **Not shown by this run:** a live news scan (robots.txt on the Samsung site often times out), a real-endpoint test of `openai_compat`, and any open-weights model.

## Transcript

# Journey on the Meta backend, 2026-10-28 00:36
Synthetic demo stock list, throwaway database. Model output below is real; every number is computed by code.

## 1. Upload (demo file, same path as a real upload)
rows 200, kept 197, blocked 3; money at risk 195,939.39 USD
alert source: 'Demo article (synthetic)' (demo_synthetic), published 2026-10-26, fetched 2026-10-28; news list: 11 items (['Demo article (synthetic)', 'Stored copy'])
- [PASS] the demo alert carries a labelled synthetic source with published and fetched dates
- [PASS] the news list shows the real items as 'Stored copy' with their true fetch date

## 2. Daily brief
(4.4 s) source=model

> $195,939 tied up as launch alert stays in view.
> - Launch alert for Samsung TVs remains announced with no date provided.
> - Money at risk with no plan yet totals $195,939.
> - Dead stock ties up $99,011 in 29 items.
> - Slow stock ties up $96,928 in 19 items, with 7 launch-family items dead or slow.
- [PASS] daily brief written by the model, not the fallback: source=model

## 3. Chat before help (alert first)
(5.8 s) agents: router:model:1626ms, inventory:model:4118ms, inventory:model:4118ms, inventory:model:4113ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Announced, no launch date given in the article. Want me to plan what to do with them?

cards: ['alert', 'item', 'item', 'item']
- [PASS] first event is a status
- [PASS] alert card then item cards, and no plan yet
- [PASS] reply ends with the question
- [PASS] reply is the alert sentences only; item detail is in the cards
- [PASS] inventory explanations used the model: router:model:1626ms, inventory:model:4118ms, inventory:model:4118ms, inventory:model:4113ms
- [PASS] every item card is a real SKU in stock

## 4. Help, then the plan in the chat
(2.1 s) agents: router:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, recommender:model:1743ms, recommender:model:1748ms, recommender:model:2086ms

> Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Announced, no launch date given in the article. Here is one option for each item. You decide: nothing changes until you accept.
- TV-SAM-0040 Samsung QN85D Neo QLED 65 inch: **Bundle**; reasons: Slow stock with 150 days of stock needs faster movement; Bundling is allowed for this SKU; Supplier return window is closed
- TV-SAM-0060 Samsung Q60D QLED 85 inch: **Return**; reasons: Item is slow stock; Return window is still open; Has high days of stock
- TV-SAM-0032 Samsung QN90D Neo QLED 43 inch: **Bundle**; reasons: Slow stock with 540 days of stock and 83 days since last sale; Discount blocked by MAP and return window closed; Bundling allowed
- [PASS] one option per item, all in the allowed set
- [PASS] recommender used the model: router:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, inventory:model:0ms:cached, recommender:model:1743ms, recommender:model:1748ms, recommender:model:2086ms
- [PASS] no discount is offered on the item whose discount would go below its minimum advertised price
- [PASS] every plan card carries its citations (numbers were checked by code before display)

## 5. Accept, then undo
- [PASS] accept lowers money at risk by that item's tied-up money: 19593939 -> 18529049
- [PASS] undo restores it

## 6. A question about one item
(1.5 s) agents: router:model:1425ms, inventory:model:0ms:cached

> You have 10 on the shelf with $10,649 tied up. The item is slow stock with 19 days since last sale and 150 days of stock at the current pace, and it is not dead stock. The supplier return window is closed or none.
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

> Not enough data: I don't have a price or a date for that. Loreg only knows your own stock numbers and the dated news it has stored, so it cannot say what a product will cost, when it will ship or what a rival charges. This source is a synthetic demo article, not real news. What is stored: Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Announced, no launch date given in the article. Want me to plan what to do with them?
- [PASS] an unknowable question gets 'not enough data' without a model call
- [PASS] it cites only stored figures and invents no date
date question on the synthetic article -> (0.1 s) agents: none

> From your stored news only: This source is a synthetic demo article, not real news. Samsung TVs may be affected by a newer line. 7 of your Samsung TVs are dead or slow, with $30,989 tied up. Source: Loreg demo (synthetic) (Demo article (synthetic)), published 2026-10-26, fetched 2026-10-28. Announced, no launch date given in the article. That article gives no launch date, so I cannot say when. Want me to plan what to do with them?
- [PASS] a date question is answered 'From your stored news only' and says the source is a synthetic demo article, without a model call
- [PASS] it makes up no launch date

## Result: 23 of 23 checks passed
