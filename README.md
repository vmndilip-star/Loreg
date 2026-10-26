# Loreg

**A smart log and register for small electronics and appliance shops.**

## Core idea

A small shop owner often has money tied up in stock that has stopped selling, and every new product launch makes that stock harder to move. Loreg watches the shop's inventory, sales and market news, and tells the owner which shelf stock is at risk **before the loss grows**, so they can decide what to do with it.

("Stock" always means shelf inventory, never securities. Loreg gives no investment advice.)

## What Loreg does

- **Finds dead and slow stock** from inventory and sales data, and shows how much money is tied up.
- **Loreg Watch:** monitors manufacturer and market news for launches that put current stock at risk.
- **Loreg Daily:** a short morning summary.
- **Ask Loreg:** a chat for questions about the shop's stock.
- **One recommended action per at-risk item** (discount, bundle, return or hold). The owner accepts or declines.

## Principles

- **Alert first, then offer help.** A discount is never the first thing shown.
- **The owner makes the final call.** Recommendations are options with reasons.
- **Everything is cited.** Each alert or recommendation names the SKUs, metrics and dated sources it used, or says "not enough data".
- **Numbers come from code, not the model.** Dead-stock math is deterministic Python; the language model only explains and recommends from numbers the code computed.
- **Web content is untrusted data.** Instructions found in fetched articles are never followed.

## Scope

TVs, washing machines, washer-dryers, refrigerators, phones and laptops, for US shops.

## Hackathon

Built solo for the Meta Global AI Developer Hackathon (indie developer track, "Build for Your World").

> **Prior work disclosure:** the planning and the early synthetic dataset generators predate the event. *(Marker for where hackathon work starts: to be added.)*

## Status

Early stage. Planning is done and a synthetic dataset exists; application code is in progress.
