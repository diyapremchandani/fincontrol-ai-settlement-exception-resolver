"""
STEP 3b - Decision gate.

Deciding WHAT to do with a resolution is a separate question from working out
what happened. Keeping the gate out of the model means the policy is auditable
and tunable without touching the prompt.

Three independent reasons to put an item in front of a human:

  1. EXPLAINABILITY  The resolver returned UNRESOLVABLE. You cannot post an
                     entry you have just declared unexplainable, however
                     confident the model is about being stuck.

  2. CONFIDENCE      Self-reported confidence below threshold.

  3. MATERIALITY     The amount is large enough that a finance team would
                     want eyes on it regardless of how sure the machine is.
                     This is standard audit practice, and it is the rule that
                     actually does the work: language models are chronically
                     overconfident, so a confidence-only gate rarely fires.

Re-running this does not cost any API calls. It only re-reads resolutions.json.
"""

import json

CONFIDENCE_THRESHOLD = 0.75
MATERIALITY_LIMIT = 2000.00      # rupees; above this a human signs off


def gate(r):
    """Return (decision, reasons). Any single trigger sends it to a human."""
    reasons = []
    if r["reason_code"] == "UNRESOLVABLE":
        reasons.append("not explainable")
    if float(r.get("confidence", 0)) < CONFIDENCE_THRESHOLD:
        reasons.append(f"confidence {float(r['confidence']):.2f}")
    if abs(float(r.get("rupee_impact", 0) or 0)) >= MATERIALITY_LIMIT:
        reasons.append(f"material: Rs {abs(float(r['rupee_impact'])):,.2f}")
    return ("ESCALATE" if reasons else "AUTO_POST"), reasons


def main():
    results = json.load(open("resolutions.json"))
    counts = {"AUTO_POST": 0, "ESCALATE": 0}
    trigger_tally = {}

    print("DECISION GATE")
    print(f"  confidence threshold  {CONFIDENCE_THRESHOLD}")
    print(f"  materiality limit     Rs {MATERIALITY_LIMIT:,.2f}\n")

    for r in results:
        decision, reasons = gate(r)
        r["decision"] = decision
        r["escalation_reasons"] = reasons
        counts[decision] += 1
        for reason in reasons:
            key = ("explainability" if reason.startswith("not") else
                   "confidence" if reason.startswith("confidence") else
                   "materiality")
            trigger_tally[key] = trigger_tally.get(key, 0) + 1

        mark = "post" if decision == "AUTO_POST" else "ESCL"
        why = ("  <- " + "; ".join(reasons)) if reasons else ""
        print(f"  {r['settlement_id']}  {mark}  {r['reason_code']:<22}"
              f" conf {float(r['confidence']):.2f}{why}")

    json.dump(results, open("resolutions.json", "w"), indent=2)

    n = len(results)
    print(f"\n  auto-posted  {counts['AUTO_POST']}/{n}")
    print(f"  escalated    {counts['ESCALATE']}/{n}")
    if trigger_tally:
        print("  triggered by:", ", ".join(f"{k} x{v}" for k, v in sorted(trigger_tally.items())))
    else:
        print("  no gate fired - review your thresholds before trusting this")


if __name__ == "__main__":
    main()
