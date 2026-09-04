"""
STEP 2 - Deterministic matcher.

Three-way reconciliation across the sources:

    LEG 1   pg_transactions  ->  settlement_report
            Recompute what each payment SHOULD have settled at, using the
            rate card, instead of trusting the settlement report. Any gap
            here is an adjustment: a refund, a chargeback, or something odd.

    LEG 2   settlement_report  ->  bank_statement
            Roll settlements up by UTR and compare against what the bank
            actually credited inside the reconciliation period.

This file is deliberately dumb. It decides only MATCHED or EXCEPTION.
It never guesses WHY something broke - that is the resolver's job in
Step 3. Keeping the two apart is what makes the accuracy score honest:
the rules cannot quietly do the AI's work for it.
"""

import csv
import json
from collections import defaultdict
from datetime import date

MDR_RATE = 0.02
GST_ON_MDR = 0.18
TDS_194O_RATE = 0.01

# The month being closed, read from the dataset rather than hardcoded.
# Anything crediting after this date has not landed in the period.
with open("period.json") as _f:
    PERIOD_END = date.fromisoformat(json.load(_f)["period_end"])

TOLERANCE = 0.005          # half a paisa - anything above this is a real gap


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def expected_net(gross, is_marketplace):
    """Independently recompute the net payout for one payment."""
    gross_p = round(gross * 100)
    mdr = round(gross_p * MDR_RATE)
    gst = round(mdr * GST_ON_MDR)
    tds = round(gross_p * TDS_194O_RATE) if is_marketplace else 0
    return (gross_p - mdr - gst - tds) / 100


def reconcile():
    pg = {r["payment_id"]: r for r in load("pg_transactions.csv")}
    settle = load("settlement_report.csv")
    bank = load("bank_statement.csv")

    # ---------------- LEG 1: payment level ----------------
    leg1_gap = defaultdict(float)      # settlement_id -> total unexplained gap
    flagged_payments = defaultdict(list)

    for row in settle:
        p = pg[row["payment_id"]]
        exp = expected_net(float(p["gross_amount"]), p["order_type"] == "marketplace")
        actual = float(row["net_settled"])
        gap = round(exp - actual, 2)
        if abs(gap) > TOLERANCE:
            leg1_gap[row["settlement_id"]] += gap
            flagged_payments[row["settlement_id"]].append({
                "payment_id": row["payment_id"],
                "pg_status": p["status"],
                "gross_amount": float(p["gross_amount"]),
                "expected_net": exp,
                "reported_net": actual,
                "gap": gap,
                "adjustment_on_report": float(row["adjustment"]),
            })

    # ---------------- LEG 2: batch level ----------------
    batches = defaultdict(lambda: {"net": 0.0, "n": 0, "date": None})
    for row in settle:
        b = batches[row["utr"]]
        b["net"] += float(row["net_settled"])
        b["n"] += 1
        b["date"] = row["settled_date"]
        b["settlement_id"] = row["settlement_id"]

    credits = defaultdict(lambda: {"amount": 0.0, "count": 0, "dates": []})
    late = defaultdict(list)
    for row in bank:
        vd = date.fromisoformat(row["value_date"])
        target = late[row["utr"]] if vd > PERIOD_END else None
        if target is not None:
            target.append(row["value_date"])
            continue
        c = credits[row["utr"]]
        c["amount"] += float(row["credit_amount"])
        c["count"] += 1
        c["dates"].append(row["value_date"])

    # Bank references that match no settlement at all.
    orphan_utrs = [u for u in list(credits) + list(late) if u not in batches]

    # ---------------- classify ----------------
    matched, exceptions = [], []

    for utr, b in batches.items():
        sid = b["settlement_id"]
        c = credits.get(utr)
        bank_amt = round(c["amount"], 2) if c else None
        gap2 = round(b["net"] - bank_amt, 2) if c else None
        l1 = round(leg1_gap.get(sid, 0.0), 2)

        clean = (
            c is not None
            and c["count"] == 1
            and abs(gap2) <= TOLERANCE
            and abs(l1) <= TOLERANCE
        )

        if clean:
            matched.append({"settlement_id": sid, "utr": utr})
            continue

        exceptions.append({
            "settlement_id": sid,
            "utr": utr,
            "settled_date": b["date"],
            "payments_in_batch": b["n"],
            "settlement_net_total": round(b["net"], 2),
            "bank_credit_in_period": bank_amt,
            "bank_credit_count": c["count"] if c else 0,
            "bank_value_dates": c["dates"] if c else [],
            "credits_after_period_end": late.get(utr, []),
            "leg1_gap_pg_vs_settlement": l1,
            "leg2_gap_settlement_vs_bank": gap2,
            "flagged_payments": flagged_payments.get(sid, []),
            "similar_bank_references": [
                u for u in orphan_utrs if u[:10] == utr[:10]
            ],
        })

    return matched, exceptions


if __name__ == "__main__":
    matched, exceptions = reconcile()
    total = len(matched) + len(exceptions)

    with open("exceptions.json", "w") as f:
        json.dump(exceptions, f, indent=2)

    print("DETERMINISTIC PASS")
    print(f"  batches processed   {total}")
    print(f"  auto-matched        {len(matched)}  ({len(matched)/total:.0%})")
    print(f"  exceptions raised   {len(exceptions)}  ({len(exceptions)/total:.0%})")
    print("\nThe matched ones need no human. The exceptions are the actual job.\n")

    for e in exceptions:
        bits = []
        if e["bank_credit_in_period"] is None:
            bits.append("no credit in period")
        if e["bank_credit_count"] > 1:
            bits.append(f"{e['bank_credit_count']} credits")
        if e["leg2_gap_settlement_vs_bank"]:
            bits.append(f"bank gap {e['leg2_gap_settlement_vs_bank']:+.2f}")
        if e["leg1_gap_pg_vs_settlement"]:
            bits.append(f"payment gap {e['leg1_gap_pg_vs_settlement']:+.2f}")
        print(f"  {e['settlement_id']}  " + ", ".join(bits))
