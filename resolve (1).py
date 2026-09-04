"""
STEP 3 - AI exception resolver.

Takes the exceptions the deterministic matcher could not clear and, for each
one, works out WHY it broke. Returns a reason code, a confidence, the rupee
impact, and a recommended action.

Two design decisions worth defending:

  1. The resolver never sees ground_truth.csv. It reasons only from the
     financial evidence the matcher hands it.

  2. Nothing is auto-posted on a low-confidence answer. Below the threshold
     the item is escalated to a human instead. An accounting tool that
     quietly guesses is worse than one that admits it does not know.
"""

import json
import os
import re
import time
from getpass import getpass

from google import genai

CONFIDENCE_THRESHOLD = 0.75    # below this we escalate rather than post
CALL_SPACING = 4.0             # seconds between calls, to respect free-tier RPM
MAX_RETRIES = 3

# Preference order. The first one the key can actually reach wins.
MODEL_PREFERENCE = [
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

REASON_CODES = {
    "FEE_GST_TDS_GAP":
        "The whole difference is standard deductions: MDR, GST charged on "
        "that MDR, and TDS u/s 194-O on marketplace orders. Nothing is wrong.",
    "TIMING_T_PLUS_2":
        "The money is not missing, it just settled after the period closed. "
        "It will appear in the next period.",
    "PARTIAL_REFUND_NETTED":
        "A partial refund on one payment was deducted from the batch, so the "
        "payout is lower than the gross implies.",
    "FULL_REFUND_NETTED":
        "A payment was refunded in full and netted off the batch.",
    "CHARGEBACK_DEDUCTION":
        "A chargeback was debited against the batch. Unlike a refund it is "
        "not tied to a payment marked refunded in the gateway data.",
    "DUPLICATE_BANK_CREDIT":
        "The bank credited the same reference more than once. The merchant "
        "holds money it is not entitled to.",
    "UTR_MISMATCH":
        "The money arrived but the bank reference is malformed or truncated, "
        "so the automated join failed. A near-identical reference exists.",
    "ROUNDING_PAISE":
        "A sub-rupee difference from rounding on fee computation. Immaterial.",
    "UNRESOLVABLE":
        "The evidence does not support any of the above. Do not guess.",
}

SYSTEM_BRIEF = f"""You are a reconciliation analyst for an Indian merchant on a
payment aggregator. A rules engine has already cleared every clean settlement.
You only see the ones it could not resolve.

RATE CARD in force:
  MDR             2% of gross
  GST             18%, charged on the MDR amount itself (not on gross)
  TDS u/s 194-O   1% of gross, on marketplace orders only
  Settlement      T+2

HOW TO READ THE EVIDENCE:
  leg1_gap_pg_vs_settlement  gross minus the rate card, versus what the
                             report says was settled. A gap here means an
                             adjustment was applied at payment level.
  leg2_gap_settlement_vs_bank  what the report promised, versus what the
                             bank actually credited inside the period.
  A positive gap means less money arrived than expected.
  A negative gap means more money arrived than expected.

REASON CODES, pick exactly one:
{chr(10).join(f"  {k}: {v}" for k, v in REASON_CODES.items())}

Return ONLY a JSON object, no markdown fence, no commentary:
{{
  "reason_code": "<one code from the list>",
  "confidence": <float 0 to 1>,
  "rupee_impact": <float, the amount at stake>,
  "evidence": "<the specific numbers that led you here, one sentence>",
  "recommended_action": "<what a finance team should actually do>"
}}

Be strict. If the evidence does not clearly fit a code, return UNRESOLVABLE
with low confidence. A wrong answer posted with confidence costs far more
than an honest escalation."""


def pick_model(client):
    """Find a model this API key can actually use, newest preferred."""
    available = {m.name.split("/")[-1] for m in client.models.list()}
    for want in MODEL_PREFERENCE:
        for got in available:
            if got.startswith(want):
                return got
    flash = sorted(m for m in available if "flash" in m)
    if flash:
        return flash[0]
    raise RuntimeError(f"No usable model found. Key can see: {sorted(available)}")


def parse_json(text):
    """Models sometimes wrap JSON in a fence despite instructions."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"No JSON found in: {text[:200]}")
    return json.loads(match.group(0))


def resolve_one(client, model, exception):
    prompt = f"{SYSTEM_BRIEF}\n\nEXCEPTION RECORD:\n{json.dumps(exception, indent=2)}"
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            out = parse_json(resp.text)
            if out["reason_code"] not in REASON_CODES:
                raise ValueError(f"Unknown code {out['reason_code']}")
            out["confidence"] = float(out["confidence"])
            return out
        except Exception as err:
            last = err
            time.sleep(3 * (attempt + 1))     # back off and retry
    return {
        "reason_code": "UNRESOLVABLE",
        "confidence": 0.0,
        "rupee_impact": 0.0,
        "evidence": f"resolver failed after {MAX_RETRIES} attempts: {last}",
        "recommended_action": "escalate to a human",
        "error": True,
    }


def main():
    key = os.environ.get("GEMINI_API_KEY") or getpass("Paste your Gemini API key: ")
    client = genai.Client(api_key=key)

    model = pick_model(client)
    print(f"using model: {model}\n")

    exceptions = json.load(open("exceptions.json"))
    results = []

    for i, exc in enumerate(exceptions, 1):
        out = resolve_one(client, model, exc)
        posted = out["confidence"] >= CONFIDENCE_THRESHOLD
        out["settlement_id"] = exc["settlement_id"]
        out["decision"] = "AUTO_POST" if posted else "ESCALATE"
        results.append(out)

        flag = "post " if posted else "ESCL "
        print(f"  [{i:2d}/{len(exceptions)}] {exc['settlement_id']}  {flag}"
              f"{out['reason_code']:<22} conf {out['confidence']:.2f}")

        if i < len(exceptions):
            time.sleep(CALL_SPACING)

    json.dump(results, open("resolutions.json", "w"), indent=2)
    n_esc = sum(1 for r in results if r["decision"] == "ESCALATE")
    print(f"\nresolved {len(results)} exceptions -> resolutions.json")
    print(f"auto-posted {len(results)-n_esc}, escalated {n_esc}")


if __name__ == "__main__":
    main()
