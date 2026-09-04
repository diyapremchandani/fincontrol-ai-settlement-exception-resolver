# Settlement Exception Resolver

**Razorpay AI Buildathon — Track 04, AI Finance Controller**

Reconciliation is mostly solved. The exceptions are not. This resolves the exceptions.

---

## The problem

A merchant on a payment aggregator holds three records of the same money: what the
gateway captured, what the aggregator says it settled, and what the bank actually
credited. These three never fully agree.

Most of the disagreement is trivial and a join clears it. The remainder is not, and
it does not fail cleanly. A batch is short by ₹1,302 because a chargeback was
debited against it. A settlement is missing because it crossed the month-end
boundary. A payout is lower than the gross implies because a partial refund was
netted off. Each of these needs someone who understands Indian payment economics to
sit with a spreadsheet and work it out.

That last part is the job automated here.

## What this does

A rules engine clears everything that reconciles exactly and hands off only what it
cannot. An LLM then works each remaining item, using the financial evidence, and
returns a reason, the amount at stake, and what to do. A separate policy gate
decides whether that answer is safe to post or belongs in front of a human.

Accuracy is measured against a held-out answer key the model never sees.

## Results

Run over 467 payment lines across 60 settlement batches:

| | |
|---|---|
| Batches cleared by rules | 48 / 60 (80%) |
| Exceptions raised | 12 |
| False positives from the matcher | 0 |
| Exceptions missed by the matcher | 0 |
| Reason code correct | 10 / 12 (83%) |
| Auto-posted | 5 |
| Auto-posted **correct** | 5 / 5 (100%) |
| Referred to a human | 7 (58%) |
| **Posted in error** | **0** |

Accuracy by reason code:

| Reason | Correct |
|---|---|
| partial refund netted | 2/2 |
| full refund netted | 1/1 |
| duplicate bank credit | 1/1 |
| UTR mismatch | 1/1 |
| rounding paise | 2/2 |
| timing, T+2 across period end | 2/2 |
| unresolvable | 1/1 |
| **chargeback deduction** | **0/2** |

The headline number is not 83%. It is **zero posted in error**. Both wrong answers
were referred to a human rather than written to the books. An escalated mistake
costs a few minutes; a posted one misstates the ledger.

## Key finding

**Chargebacks cannot be resolved from settlement and bank data alone.**

Both chargeback batches were answered `unresolvable` and referred. That looks like a
model failure. It is not.

In this dataset a chargeback reduces the bank credit with no supporting record in
any of the three sources — which is exactly what a genuinely unexplained shortfall
looks like. The two classes are observationally identical. No model could separate
them, and guessing would have produced a confident wrong journal entry.

Closing this requires a fourth source: the disputes feed. That requirement was found
by measuring the system, not by anticipating it, and it is the clearest argument for
scoring against an answer key rather than demoing a case that works.

## How it works

```
pg_transactions ─┐
settlement_report ├─► deterministic matcher ─► 48 cleared
bank_statement ──┘         (two legs)         └─► 12 exceptions
                                                      │
                                                      ▼
                                              LLM exception resolver
                                              reason · confidence ·
                                              impact · action
                                                      │
                                                      ▼
                                               decision gate
                                        explainability │ confidence │ materiality
                                                      │
                                          ┌───────────┴───────────┐
                                       auto-post              refer to human
                                                      │
                                                      ▼
                                       scorecard vs held-out answer key
```

**Leg 1 — payments.** For every settled payment the matcher recomputes the expected
payout from the rate card rather than trusting the settlement report. A gap here
means an adjustment was applied at payment level.

**Leg 2 — batches.** Settlements are rolled up by UTR and compared against what the
bank credited inside the reconciliation period.

The rate card in force: MDR 2%, GST 18% charged on the MDR amount itself, TDS u/s
194-O 1% on marketplace orders only, settlement T+2. Getting GST-on-fee and the
marketplace-only TDS right is what separates a real gap from a normal deduction.

## Where the LLM is used, and where it is not

The model classifies and explains. It does nothing else.

| Stage | Handled by | Why |
|---|---|---|
| Matching | Rules | Deterministic, auditable, and free |
| Working out why an item broke | LLM | Requires reading evidence in context |
| Deciding to post or refer | Rules | Policy must be tunable without touching a prompt |
| Scoring | Rules | The grader cannot be the thing being graded |

The resolver never sees `ground_truth.csv`. It reasons only from the evidence the
matcher assembles: both leg gaps, bank credit count and dates, credits falling after
period end, payment-level flags, and any near-identical bank reference.

It picks from a fixed set of nine reason codes, one of which is `unresolvable`. This
is closed-set classification, not open-ended reasoning — a deliberate trade for
scoring that means something.

## The decision gate

Three independent reasons to put an item in front of a person. Any one triggers.

1. **Explainability** — the resolver returned `unresolvable`. You cannot post an
   entry you have declared unexplainable, however confident the model is about
   being stuck.
2. **Confidence** — self-reported confidence below 0.75.
3. **Materiality** — impact at or above ₹2,000, regardless of confidence. Standard
   audit practice.

On this run: explainability fired 3 times, materiality 5 times, **confidence never
fired at all**.

That last point came out of testing. The first run auto-posted all twelve, because
the model reported high confidence on every single item. Language models are
chronically overconfident, so a confidence-only gate is decoration. Materiality is
the rule that does the work — and it is a finance control, not a machine-learning
one.

## Honest caveats

- The data is synthetic. Real settlement files carry noise this dataset does not.
- It is deliberately exception-heavy at 20%. A real merchant runs nearer 2–5%. Every
  reason code needed enough instances to be scored.
- The matcher's zero false positives is measured on data whose faults are known by
  construction. It is not a claim about production.
- Nine reason codes cover the common cases, not all of them.
- Thresholds (0.75, ₹2,000) are reasoned starting points, not tuned optima.

## Run it

Everything runs in Google Colab. No local install, no paid API.

1. Get a free Gemini API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Open a new Colab notebook and create one cell per step below.

```
Cell 1   !pip install -q google-genai
Cell 2   generate_data.py     builds the three sources + answer key
Cell 3   match.py             deterministic pass, writes exceptions.json
Cell 4   resolve.py           LLM pass, prompts for your key, writes resolutions.json
Cell 5   gate.py              applies the decision gate, no API calls
Cell 6   score.py             scorecard vs answer key
Cell 7   report.py            writes report.html
```

Paste each file as-is. Colab runs `if __name__ == "__main__"` blocks automatically.

`resolve.py` reads the key with `getpass`, so it is never written into the notebook.
It also auto-detects an available Flash model instead of hardcoding a name, since
Google's free-tier lineup changes. Calls are spaced 4 seconds apart to stay inside
free-tier rate limits; the twelve exceptions take about a minute.

The generator is seeded, so the numbers above reproduce exactly.

## Files

| File | Role |
|---|---|
| `generate_data.py` | Three synthetic sources plus the held-out answer key |
| `match.py` | Two-leg deterministic matcher |
| `resolve.py` | LLM exception resolver |
| `gate.py` | Post-or-refer policy gate |
| `score.py` | Scorecard against the answer key |
| `report.py` | Renders `report.html`, a reconciliation working paper |

## What I would build next

1. **Ingest the disputes feed.** The one limitation this project actually proved.
2. **Calibrate the confidence signal** against outcomes, so it earns a place in the
   gate instead of sitting there unused.
3. **Set materiality from the merchant's own settlement distribution** rather than a
   flat ₹2,000.

---

Built for the Razorpay AI Buildathon.
