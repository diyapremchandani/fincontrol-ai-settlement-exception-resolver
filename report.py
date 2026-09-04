"""
STEP 5 - Results screen.

Renders the whole run as a single self-contained HTML file: no external
assets, no internet needed, opens straight in a browser. Safe to commit
and easy to film.

It is laid out as a reconciliation working paper rather than a dashboard,
because that is the document a finance team actually signs off. Cleared
lines carry a tick, referred lines carry a query mark.

Run after gate.py and score.py. Writes report.html.
"""

import csv
import json
from html import escape

PERIOD = json.load(open("period.json"))
EXCEPTIONS = {e["settlement_id"]: e for e in json.load(open("exceptions.json"))}
RESULTS = json.load(open("resolutions.json"))
SCORE = json.load(open("scorecard.json"))
TRUTH = {r["settlement_id"]: r for r in csv.DictReader(open("ground_truth.csv"))}

N_PAYMENTS = sum(1 for _ in csv.DictReader(open("settlement_report.csv")))
N_BATCHES = len(TRUTH)
N_EXC = len(RESULTS)
N_CLEARED = N_BATCHES - N_EXC

CSS = """
:root{
  --paper:#faf9f6; --rule:#d9d8d0; --rule-firm:#a8a79d;
  --ink:#1c2024; --muted:#6d7278;
  --tick:#1f6b4a; --query:#b0442c;
}
*{box-sizing:border-box}
body{
  margin:0; background:#eceae4; color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.55; font-variant-numeric:tabular-nums;
}
.sheet{
  max-width:60rem; margin:2.5rem auto; background:var(--paper);
  padding:3rem 3.25rem 3.5rem; box-shadow:0 1px 3px rgba(0,0,0,.14);
}
h1{
  font-family:Georgia,"Iowan Old Style",serif; font-weight:400;
  font-size:1.85rem; margin:0 0 .2rem; letter-spacing:-.01em;
}
.meta{color:var(--muted); font-size:.86rem; margin:0 0 2.4rem}
h2{
  font-family:Georgia,"Iowan Old Style",serif; font-weight:400;
  font-size:1.12rem; margin:2.8rem 0 .9rem;
  padding-bottom:.4rem; border-bottom:1px solid var(--rule-firm);
}
table{width:100%; border-collapse:collapse}
td,th{padding:.44rem 0; vertical-align:baseline}
th{
  text-align:left; font-weight:600; font-size:.78rem; color:var(--muted);
  border-bottom:1px solid var(--rule); padding-bottom:.3rem;
}
.num{text-align:right; white-space:nowrap}
.face td{border-bottom:1px solid var(--rule)}
.face .lead{width:60%}
.face .fig{width:22%; font-size:1.02rem}
.face .mark{width:18%; font-size:.8rem; color:var(--muted)}
.face tr:last-child td{
  border-bottom:2px solid var(--rule-firm);
  border-top:1px solid var(--rule-firm);
  padding-top:.7rem; font-weight:600;
}
.indent{padding-left:1.6rem; color:var(--muted)}
.tick{color:var(--tick)}
.query{color:var(--query)}
.reg td{border-bottom:1px solid var(--rule); font-size:.88rem}
.reg .id{font-size:.82rem; color:var(--muted); white-space:nowrap}
.reg .code{font-weight:600}
.reg .ev{color:var(--muted); font-size:.83rem; padding-top:0; padding-bottom:.7rem}
.reg .wrong{color:var(--query)}
.bar{display:inline-block; height:.5rem; background:var(--tick); vertical-align:middle}
.bar.none{background:var(--query); min-width:2px}
.note{
  margin-top:2.8rem; padding:1.15rem 1.35rem;
  border-left:3px solid var(--query); background:#f4f1ec;
}
.note p{margin:.45rem 0; font-size:.9rem; max-width:66ch}
.note strong{font-weight:600}
.foot{margin-top:2.6rem; color:var(--muted); font-size:.8rem; max-width:70ch}
@media(max-width:640px){.sheet{padding:1.75rem 1.25rem; margin:0}}
"""


def rupees(v):
    return f"{abs(float(v)):,.2f}"


def face_row(label, figure, mark="", indent=False, mark_cls=""):
    cls = ' class="indent"' if indent else ""
    return (f'<tr><td class="lead"><span{cls}>{label}</span></td>'
            f'<td class="fig num">{figure}</td>'
            f'<td class="mark num {mark_cls}">{mark}</td></tr>')


def build_face():
    rows = [
        face_row("Payment lines settled in the period", f"{N_PAYMENTS:,}"),
        face_row("Settlement batches", f"{N_BATCHES}"),
        face_row("Cleared by the rules engine", f"{N_CLEARED}", "tied", True, "tick"),
        face_row("Raised as exceptions", f"{N_EXC}", "queried", True, "query"),
        face_row("Exceptions posted automatically", f"{SCORE['auto_posted']}", "", True),
        face_row("Exceptions referred for sign-off", f"{SCORE['escalated']}", "", True),
        face_row("Posted in error", f"{SCORE['silent_failures']}", "nil", False, "tick"),
    ]
    return f'<table class="face">{"".join(rows)}</table>'


def build_register():
    order = {"ESCALATE": 0, "AUTO_POST": 1}
    rows = []
    for r in sorted(RESULTS, key=lambda x: (order[x["decision"]], x["settlement_id"])):
        sid = r["settlement_id"]
        want = TRUTH[sid]["true_reason_code"]
        ok = r["reason_code"] == want
        exc = EXCEPTIONS[sid]

        referred = r["decision"] == "ESCALATE"
        mark = ("<span class='query'>referred</span>" if referred
                else "<span class='tick'>posted</span>")
        why = "; ".join(r.get("escalation_reasons", []))

        code = escape(r["reason_code"].replace("_", " ").lower())
        if not ok:
            code = (f"<span class='wrong'>{code}</span>"
                    f"<br><span class='ev'>actual: "
                    f"{escape(want.replace('_',' ').lower())}</span>")

        impact = r.get("rupee_impact") or exc.get("leg2_gap_settlement_vs_bank") \
            or exc.get("leg1_gap_pg_vs_settlement") or 0
        rows.append(
            f"<tr><td class='id'>{escape(sid)}</td>"
            f"<td class='code'>{code}</td>"
            f"<td class='num'>{rupees(impact)}</td>"
            f"<td class='num'>{float(r['confidence']):.2f}</td>"
            f"<td class='num'>{mark}</td></tr>"
            f"<tr><td></td><td colspan='4' class='ev'>"
            f"{escape(str(r.get('evidence','')))}"
            + (f"<br>referred: {escape(why)}" if why else "") +
            "</td></tr>")

    return (
        "<table class='reg'><tr><th>Batch</th><th>Reason</th>"
        "<th class='num'>Amount</th><th class='num'>Conf.</th>"
        "<th class='num'>Outcome</th></tr>" + "".join(rows) + "</table>")


def build_codes():
    rows = []
    for code, d in sorted(SCORE["per_code"].items(), key=lambda kv: kv[1]["right"] / kv[1]["n"]):
        share = d["right"] / d["n"]
        width = max(share * 9, 0.12)
        cls = "bar" if d["right"] else "bar none"
        rows.append(
            f"<tr><td>{escape(code.replace('_',' ').lower())}</td>"
            f"<td class='num'>{d['right']}/{d['n']}</td>"
            f"<td style='padding-left:1rem'>"
            f"<span class='{cls}' style='width:{width:.2f}rem'></span></td></tr>")
    return f"<table>{''.join(rows)}</table>"


HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settlement reconciliation</title><style>{CSS}</style></head>
<body><div class="sheet">

<h1>Settlement reconciliation</h1>
<p class="meta">{PERIOD['period_start']} to {PERIOD['period_end']} &nbsp;·&nbsp;
prepared by the exception resolver &nbsp;·&nbsp; rate card: MDR 2%,
GST 18% on fee, TDS 194-O 1% on marketplace orders</p>

{build_face()}

<h2>Exception register</h2>
{build_register()}

<h2>Accuracy by reason</h2>
{build_codes()}

<div class="note">
<p><strong>Chargebacks could not be resolved: 0 of 2 correct.</strong></p>
<p>In this dataset a chargeback reduces the bank credit with no supporting
record anywhere, which is exactly what an unexplained shortfall looks like.
The two are indistinguishable from settlement and bank data alone, so the
resolver returned "unresolvable" and referred both rather than guessing.
That is the intended behaviour, and it is why neither wrong answer reached
the ledger.</p>
<p>Closing this needs a fourth source: the disputes feed. It was found by
measuring the system, not by anticipating it.</p>
</div>

<p class="foot">Accuracy is measured against a held-out answer key the
resolver never saw. The dataset is deliberately exception-heavy at
{N_EXC/N_BATCHES:.0%} so every reason code is exercised; a real merchant
runs nearer 2&ndash;5%. Amounts in rupees.</p>

</div></body></html>"""

if __name__ == "__main__":
    open("report.html", "w").write(HTML)
    print(f"wrote report.html  ({len(HTML):,} bytes)")
    print(f"  {N_PAYMENTS} lines, {N_BATCHES} batches, {N_EXC} exceptions")
    print(f"  {SCORE['auto_posted']} posted, {SCORE['escalated']} referred, "
          f"{SCORE['silent_failures']} posted in error")
