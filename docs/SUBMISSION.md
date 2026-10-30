# Loreg: submission description

**Track:** Meta Global AI Developer Hackathon, indie developer track ("Build for Your World").

## Description (about 300 words)

Small electronics and appliance shops often have thousands of dollars tied up in stock that stopped selling. Every new phone, TV or
washer launch makes that stock harder to move. The owner usually finds out too late, from a full shelf rather than a report.

Loreg is a smart log and register for these shops. The owner uploads the stock list they already export, and Loreg:

- checks every row and sets aside the ones it cannot trust;
- finds the dead and slow stock and shows how much money is tied up;
- watches manufacturer news for launches that put that stock at risk;
- writes a short morning brief (Loreg Daily);
- answers questions in plain words (Ask Loreg).

When the owner asks for help, Loreg proposes one action per item: a discount, a bundle, a return to the supplier, or hold. The
owner accepts or declines; nothing changes on its own.

The design rule is that **numbers come from code and words come from the model**. Every figure is plain, tested Python: money
tied up, days since the last sale, and which discounts stay above cost and the minimum advertised price. Meta's model explains
those figures and picks among the options the code allows. Every answer is checked before it is shown. Its citations must be
real facts, every number must appear in one, and the option must be allowed. A failed answer is retried once and then
replaced by a rules answer that is labelled as such. News articles are treated as untrusted data, never as instructions.

We measure this rather than assume it. On the real Meta model:
- **21 of 21** hand-worked eval cases passed, including below-cost traps, closed return windows, minimum-advertised-price limits
  and items with almost no data;
- over the demo workload, **0 of 75** agent calls fell back to the rules (p50 about 2 seconds).

The app shows which backend is answering and how many items used the rules fallback.

**Honest limits:**
- One news source.
- The demo's launch article is synthetic and labelled as such.
- All stock data is synthetic.
- There is no point-of-sale integration.
- Accepting a plan is not counted as money freed until the units sell.
- An eval pass shows the answer was safe, not that it was the best choice.
- Loreg gives no investment advice: "stock" means shelf inventory.

## Links

- Code: https://github.com/vmndilip-star/Loreg
- How to run it, the design and every limit: `README.md`
- Eval results: `docs/EVALS.md`
- Every agent, prompt, check and fallback: `docs/AGENT_FRAMEWORK_REPORT.md`
