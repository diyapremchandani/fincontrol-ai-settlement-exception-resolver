"""
STEP 4 - Scorecard.

Compares the resolver's answers against ground_truth.csv, which the resolver
never saw.

The headline number is NOT overall accuracy. It is the silent failure count:
how many wrong answers did the system post to the books without asking a
human? An escalated wrong answer costs a few minutes. A posted wrong answer
costs a misstated ledger.
"""

import csv
import json
from collections import defaultdict

truth = {r["settlement_id"]: r for r in csv.DictReader(open("ground_truth.csv"))}
results = json.load(open("resolutions.json"))

correct = posted = posted_correct = escalated = escalated_correct = 0
silent_failures = []
per_code = defaultdict(lambda: {"n": 0, "right": 0})

for r in results:
    want = truth[r["settlement_id"]]["true_reason_code"]
    got = r["reason_code"]
    ok = (got == want)

    per_code[want]["n"] += 1
    per_code[want]["right"] += ok
    correct += ok

    if r["decision"] == "AUTO_POST":
        posted += 1
        posted_correct += ok
        if not ok:
            silent_failures.append((r["settlement_id"], want, got, r["confidence"]))
    else:
        escalated += 1
        escalated_correct += ok

n = len(results)
pct = lambda a, b: f"{a/b:.0%}" if b else "n/a"

print("=" * 62)
print("RESOLVER SCORECARD".center(62))
print("=" * 62)
print(f"  exceptions resolved        {n}")
print(f"  reason code correct        {correct}/{n}   ({pct(correct, n)})")
print()
print(f"  auto-posted                {posted}")
print(f"    of which correct         {posted_correct}/{posted}   ({pct(posted_correct, posted)})")
print(f"  escalated to human         {escalated}   ({pct(escalated, n)})")
print()
print(f"  SILENT FAILURES            {len(silent_failures)}")
print("     (wrong AND posted without asking anyone)")

if silent_failures:
    print()
    for sid, want, got, conf in silent_failures:
        print(f"     {sid}: said {got}, was {want} (conf {conf:.2f})")

print()
print("  per reason code:")
for code in sorted(per_code):
    d = per_code[code]
    print(f"    {code:<24} {d['right']}/{d['n']}  {pct(d['right'], d['n'])}")

print()
print("  NOTE: the dataset is deliberately exception-heavy so every reason")
print("  code is exercised. A real merchant runs 2-5% exceptions, not 20%.")
print("=" * 62)

json.dump({
    "exceptions": n,
    "accuracy": round(correct / n, 3) if n else None,
    "auto_posted": posted,
    "auto_posted_accuracy": round(posted_correct / posted, 3) if posted else None,
    "escalated": escalated,
    "silent_failures": len(silent_failures),
    "per_code": {k: dict(v) for k, v in per_code.items()},
}, open("scorecard.json", "w"), indent=2)
print("\nsaved -> scorecard.json")
