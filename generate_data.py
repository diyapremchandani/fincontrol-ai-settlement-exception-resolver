"""
STEP 1 - Synthetic data generator.

Creates three files that a real merchant would have to reconcile:
  1. pg_transactions.csv    - what the payment gateway says happened
  2. settlement_report.csv  - what the aggregator says it paid out
  3. bank_statement.csv     - what actually landed in the bank

Plus ground_truth.csv - the correct answer for every planted discrepancy.
Because we plant them, we can score ourselves honestly later.

All money is handled in PAISE (integers) internally to avoid float errors,
and converted to rupees only when writing out.
"""

import random
import csv
import json
from datetime import date, timedelta

SEED = 42
random.seed(SEED)

# ---------------------------------------------------------------
# Indian payment economics. This is the domain core of the project.
# ---------------------------------------------------------------
MDR_RATE = 0.02        # 2% merchant discount rate charged by the aggregator
GST_ON_MDR = 0.18      # 18% GST levied on the MDR fee itself
TDS_194O_RATE = 0.01   # 1% TDS u/s 194-O, applies to marketplace orders only

START = date(2026, 7, 1)
N_SETTLEMENTS = 60      # two settlement batches a day for a month
N_ANOMALIES = 12        # deliberately over-sampled: see README
PAYMENTS_PER_SETTLEMENT = (6, 9)

METHODS = ["upi", "card", "netbanking", "wallet"]

REASON_CODES = [
    "FEE_GST_TDS_GAP",
    "TIMING_T_PLUS_2",
    "PARTIAL_REFUND_NETTED",
    "FULL_REFUND_NETTED",
    "CHARGEBACK_DEDUCTION",
    "DUPLICATE_BANK_CREDIT",
    "UTR_MISMATCH",
    "ROUNDING_PAISE",
    "UNRESOLVABLE",
]


def deductions(gross_p, is_marketplace):
    """Return (mdr, gst, tds) in paise for a gross amount in paise."""
    mdr = round(gross_p * MDR_RATE)
    gst = round(mdr * GST_ON_MDR)
    tds = round(gross_p * TDS_194O_RATE) if is_marketplace else 0
    return mdr, gst, tds


def build():
    payments, settlements, bank, truth = [], [], [], []
    pay_n = seq = 0

    # Choose which settlements carry which anomaly.
    # Most batches are clean, as in real life. A minority carry a planted
    # fault. Every reason code is represented at least once so the resolver
    # can be scored on all of them.
    pool = [c for c in REASON_CODES if c != "FEE_GST_TDS_GAP"]
    pool += ["PARTIAL_REFUND_NETTED", "TIMING_T_PLUS_2", "CHARGEBACK_DEDUCTION",
             "ROUNDING_PAISE"]
    pool = pool[:N_ANOMALIES]

    # Timing faults are a period-boundary problem by definition, so they are
    # placed on the final batches of the month. Everything else lands at random.
    timing = [c for c in pool if c == "TIMING_T_PLUS_2"]
    other = [c for c in pool if c != "TIMING_T_PLUS_2"]
    planted = {}
    tail = list(range(N_SETTLEMENTS - len(timing), N_SETTLEMENTS))
    for slot, code in zip(tail, timing):
        planted[slot] = code
    slots = random.sample(range(N_SETTLEMENTS - len(timing)), len(other))
    for slot, code in zip(sorted(slots), other):
        planted[slot] = code

    for s in range(N_SETTLEMENTS):
        sid = f"setl_{s+1:03d}"
        utr = f"UTR{random.randint(10**11, 10**12 - 1)}"
        cap_date = START + timedelta(days=s // 2)   # two batches per day
        settle_date = cap_date + timedelta(days=2)   # T+2 is the normal cycle
        anomaly = planted.get(s)
        batch_net = 0
        n_pay = random.randint(*PAYMENTS_PER_SETTLEMENT)

        for _ in range(n_pay):
            pay_n += 1
            pid = f"pay_{pay_n:04d}"
            oid = f"order_{pay_n:04d}"
            gross_p = random.randint(30000, 900000)   # Rs 300 - Rs 9,000
            marketplace = random.random() < 0.4
            mdr, gst, tds = deductions(gross_p, marketplace)
            refund_p = 0
            status = "captured"

            payments.append({
                "payment_id": pid, "order_id": oid,
                "captured_date": cap_date.isoformat(),
                "gross_amount": gross_p / 100,
                "method": random.choice(METHODS),
                "status": status,
                "order_type": "marketplace" if marketplace else "direct",
            })

            net_p = gross_p - mdr - gst - tds - refund_p
            batch_net += net_p
            seq += 1
            settlements.append({
                "settlement_id": sid, "payment_id": pid, "utr": utr,
                "settled_date": settle_date.isoformat(),
                "gross_amount": gross_p / 100,
                "mdr_fee": mdr / 100, "gst_on_fee": gst / 100,
                "tds_194o": tds / 100, "adjustment": 0.0,
                "net_settled": net_p / 100,
            })

        # ---------- plant the anomaly ----------
        credit_p = batch_net
        credit_utr = utr
        credit_date = settle_date
        note = ""

        if anomaly == "PARTIAL_REFUND_NETTED":
            row = settlements[-1]
            adj_p = round(float(row["gross_amount"]) * 100 * 0.35)
            row["adjustment"] = -adj_p / 100
            row["net_settled"] = round(float(row["net_settled"]) - adj_p / 100, 2)
            credit_p -= adj_p
            note = f"35% refund on {row['payment_id']} netted off this settlement"
            _mark(payments, row["payment_id"], "partially_refunded")

        elif anomaly == "FULL_REFUND_NETTED":
            row = settlements[-2]
            adj_p = round(float(row["net_settled"]) * 100)
            row["adjustment"] = -adj_p / 100
            row["net_settled"] = 0.0
            credit_p -= adj_p
            note = f"full refund on {row['payment_id']} netted off this settlement"
            _mark(payments, row["payment_id"], "refunded")

        elif anomaly == "CHARGEBACK_DEDUCTION":
            cb_p = random.randint(80000, 250000)
            credit_p -= cb_p
            note = f"chargeback of Rs {cb_p/100:.2f} debited against this batch"

        elif anomaly == "TIMING_T_PLUS_2":
            credit_date = settle_date + timedelta(days=3)
            note = "bank credit landed 3 days late; falls outside the period"

        elif anomaly == "UTR_MISMATCH":
            credit_utr = utr[:-2]      # bank truncated the reference
            note = "bank narration carries a truncated UTR, so the join fails"

        elif anomaly == "ROUNDING_PAISE":
            credit_p -= random.randint(1, 60)
            note = "sub-rupee rounding difference on fee computation"

        elif anomaly == "DUPLICATE_BANK_CREDIT":
            note = "same UTR credited twice by the bank"

        elif anomaly == "UNRESOLVABLE":
            credit_p -= random.randint(150000, 400000)
            note = "shortfall with no supporting document - needs a human"

        bank.append({
            "bank_txn_id": f"bnk_{len(bank)+1:04d}",
            "value_date": credit_date.isoformat(),
            "credit_amount": round(credit_p / 100, 2),
            "utr": credit_utr,
            "narration": f"RAZORPAY SETTLEMENT {credit_utr}",
        })

        if anomaly == "DUPLICATE_BANK_CREDIT":
            bank.append({
                "bank_txn_id": f"bnk_{len(bank)+1:04d}",
                "value_date": credit_date.isoformat(),
                "credit_amount": round(credit_p / 100, 2),
                "utr": credit_utr,
                "narration": f"RAZORPAY SETTLEMENT {credit_utr}",
            })

        if anomaly is None or anomaly == "FEE_GST_TDS_GAP":
            anomaly = "FEE_GST_TDS_GAP"
            note = "gross vs bank gap is entirely MDR + GST + TDS"

        truth.append({
            "settlement_id": sid, "utr": utr,
            "true_reason_code": anomaly,
            "gap_amount": round((batch_net - credit_p) / 100, 2),
            "explanation": note,
        })

    return payments, settlements, bank, truth


def _mark(payments, pid, status):
    for p in payments:
        if p["payment_id"] == pid:
            p["status"] = status
            return


def write(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  {path:28s} {len(rows):4d} rows")


if __name__ == "__main__":
    p, s, b, t = build()
    period_end = max(r["settled_date"] for r in s)
    with open("period.json", "w") as f:
        json.dump({"period_start": START.isoformat(), "period_end": period_end}, f)
    print("Generated:")
    write(p, "pg_transactions.csv")
    write(s, "settlement_report.csv")
    write(b, "bank_statement.csv")
    write(t, "ground_truth.csv")
    print(f"\n{len(s)} settlement lines across {len(t)} batches.")
    print(f"reconciliation period: {START.isoformat()} to {period_end}")
    print(f"{sum(1 for r in t if r['true_reason_code'] != 'FEE_GST_TDS_GAP')} batches carry a non-trivial exception.")
