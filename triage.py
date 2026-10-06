#!/usr/bin/env python3
"""Inbox triage demo: Jev answers questions about each email, plain rules pick the lane.

Usage:
  python3 triage.py              live run (needs TYPESAFE_API_KEY or ~/.typesafe/api_key)
  python3 triage.py --dry-run    print the request for one email, no API call
  python3 triage.py --order-check  live run, then re-ask topic with options reversed to test order bias
  python3 triage.py --selftest   render the report from the hand labels (checks the pipeline only,
                                 the report is stamped as NOT model output)
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
API_URL = os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
MODEL = os.getenv("TYPESAFE_MODEL", "jev-latest")
PRICE_PER_MTOK = 0.042  # USD, input only; output is free
MONTHLY_EMAILS = 600

STORE = "A small online skincare store in New Zealand selling cleansers, serums, moisturisers, night cream and toner."

# Docs guidance applied: one judgment per question, levels describe situations,
# boundary cases live in criteria, risk is three separate Nouls (several can apply).
QUESTIONS = {
    "topic": {
        "type": "choice",
        "instructions": "What is `email.text` mainly about?",
        "criteria": {
            "where_is_order": "An order that has not arrived yet, is late, or has tracking that is stuck",
            "damaged_or_wrong": "An item that arrived broken, leaking, missing, or not what was ordered",
            "refund_return": "Returning an item, exchanging it, cancelling an order, or getting money back",
            "product_question": "How to use a product, its ingredients, whether it suits someone, or its effects on skin or health",
            "other": "None of the above, such as discount codes, address changes, subscriptions, wholesale or business enquiries",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How soon does the sender of `email.text` need a reply?",
        "criteria": [
            "No deadline is mentioned or implied",
            "The sender wants a reply soon, but nothing goes wrong if it waits a day",
            "Something goes wrong without a reply today, such as an order about to ship, a deadline today, or a person's health",
        ],
    },
    "frustration": {
        "type": "score",
        "instructions": "How does the sender of `email.text` feel about the store?",
        "criteria": [
            "Friendly or neutral; just asking a question or letting the store know something",
            "Unhappy about a problem but polite, and still expects the store to fix it",
            "Angry, has given up on the store, or says they will stop buying",
        ],
    },
    "safety_issue": {
        "type": "noul",
        "instructions": "Does `email.text` describe possible harm to a person's body?",
        "criteria": {
            "true": "A skin reaction, rash, allergy, injury, cut, burn, or someone swallowing a product",
            "false": "No harm to any person is described; damage to items or packaging alone does not count",
        },
    },
    "legal_or_chargeback": {
        "type": "noul",
        "instructions": "Does `email.text` mention a chargeback, a bank or card dispute, a lawyer, the Commerce Commission, or consumer law?",
    },
    "public_complaint": {
        "type": "noul",
        "instructions": "Does the sender of `email.text` say they might complain publicly, such as posting a bad review or posting on social media?",
    },
    "refund_requested": {
        "type": "noul",
        "instructions": "Is the sender of `email.text` asking for their money back?",
        "criteria": {
            "true": "Asks for a refund, money back, a chargeback, or to cancel an order and be repaid",
            "false": "Does not ask for money back; asking for a replacement or exchange does not count",
        },
    },
    "is_pitch": {
        "type": "noul",
        "instructions": "Is `email.text` from a business offering to sell its own services to the store?",
        "criteria": {
            "true": "An agency, marketer, SEO seller, developer, designer or influencer offering paid work, including free audits or calls that lead to paid work",
            "false": "A customer, or a business that wants to buy or stock the store's products, such as a wholesale enquiry",
        },
    },
}
RISK_NOULS = {"safety_issue": "health_or_safety", "legal_or_chargeback": "legal_or_chargeback", "public_complaint": "public_complaint"}

# Thresholds scale with the cost of being wrong (docs: Confidence, Noul).
RISK_AT = 0.3          # missing a safety or legal issue is expensive, so flag early
PITCH_AT = 0.8         # archiving a real customer is expensive, so archive only when sure
NOUL_UNSURE = (0.3, 0.7)
TOPIC_CONF_MIN = 0.6

LANES = {
    "human_now": "Human now",
    "human_queue": "Human queue",
    "auto": "Automated",
    "archive": "Archived",
}
AUTO_ACTIONS = {
    "where_is_order": "Auto-reply with live tracking",
    "damaged_or_wrong": "Auto-reply asking for a photo, priority queue",
    "refund_return": "Auto-reply with returns form",
}


# ---------- Jev call ----------

def load_api_key() -> str:
    key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    for p in (Path.home() / ".typesafe" / "api_key", Path.home() / ".config" / "typesafe" / "api_key"):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def payload_for(email: dict, questions: dict | None = None) -> dict:
    state = {"store": STORE, "email": {"from": email["from"], "text": email["text"]}}
    return {"state": state, "model": MODEL, "questions": questions or QUESTIONS}


def reversed_topic_questions() -> dict:
    """Same questions with topic options reversed. Jev 1.13 can lean toward the first option."""
    q = json.loads(json.dumps(QUESTIONS))
    q["topic"]["criteria"] = dict(reversed(list(q["topic"]["criteria"].items())))
    return {"topic": q["topic"]}


def call_jev(email: dict, key: str, questions: dict | None = None) -> tuple[dict, int]:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload_for(email, questions)).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"TypeSafe API {exc.code} on {email['id']}: {exc.read()[:500]!r}")
    return body, round((time.perf_counter() - start) * 1000)


# ---------- rules (code owns the decision) ----------

def risks(a: dict) -> list[str]:
    return [label for q, label in RISK_NOULS.items() if a[q]["noul"] >= RISK_AT]


def decide(a: dict) -> tuple[str, str]:
    """Return (lane, reason). Order matters: safety checks run before anything automated."""
    topic, topic_conf = a["topic"]["choice"], a["topic"]["confidence"]
    pitch = a["is_pitch"]["noul"]
    refund = a["refund_requested"]["noul"]
    frustration = a["frustration"]["score"]
    urgency = a["urgency"]["score"]
    flagged = risks(a)
    lo, hi = NOUL_UNSURE

    if flagged:
        return "human_now", "Risk flagged: " + ", ".join(r.replace("_", " ") for r in flagged)
    if pitch >= PITCH_AT:
        return "archive", "Sales pitch"
    if frustration >= 1.5:
        return "human_now", "Customer close to leaving"
    if refund >= hi and frustration >= 0.75:
        return "human_now", "Upset and asking for money back"
    if topic_conf < TOPIC_CONF_MIN or lo < pitch < PITCH_AT or lo < refund < hi:
        return "human_queue", "Model unsure, left for a person"
    if topic in AUTO_ACTIONS:
        return "auto", AUTO_ACTIONS[topic]
    if urgency >= 1.5:
        return "human_now", "Needs a reply today"
    if topic == "product_question":
        return "human_queue", "Product question, reply drafted for review"
    return "human_queue", "Needs a person"


# ---------- self-test answers (pipeline check only) ----------

def fake_answers(exp: dict) -> dict:
    lane, risk = exp["lane"], exp["risk"]
    hot = 2.0 if lane == "human_now" and risk == "none" else 0.0
    a = {
        "topic": {"choice": exp["topic"], "confidence": 1.0},
        "urgency": {"score": hot, "confidence": 1.0},
        "frustration": {"score": hot, "confidence": 1.0},
        "refund_requested": {"noul": 0.0},
        "is_pitch": {"noul": 1.0 if exp["pitch"] else 0.0},
    }
    for q, label in RISK_NOULS.items():
        a[q] = {"noul": 1.0 if risk == label else 0.0}
    return a


# ---------- scoring + report ----------

def ok(got: str, exp: dict, field: str) -> bool:
    return got == exp[field] or got in exp.get(f"also_ok_{field}", [])


def needs_human(lane: str) -> bool:
    return lane.startswith("human")


def build_report(rows: list[dict], selftest: bool, order_flips: int | None) -> str:
    n = len(rows)
    lane_ok = sum(r["lane_ok"] for r in rows)
    human_ok = sum(needs_human(r["lane"]) == needs_human(r["exp"]["lane"]) for r in rows)
    missed = [r for r in rows if r["exp"]["lane"] == "human_now" and not needs_human(r["lane"])]
    tokens = sum(r["tokens"] for r in rows)
    avg_tok = tokens / n
    monthly = avg_tok * MONTHLY_EMAILS * PRICE_PER_MTOK / 1e6
    lat = sorted(r["latency_ms"] for r in rows)
    counts = {k: sum(r["lane"] == k for r in rows) for k in LANES}

    def cell(r):
        a = r["answers"]
        return (
            f"<tr class='{'' if r['lane_ok'] else 'miss'}'>"
            f"<td class='msg'>{html.escape(r['text'])}</td>"
            f"<td>{a['topic']['choice'].replace('_', ' ')}<span class='c'>{a['topic']['confidence']:.2f}</span></td>"
            f"<td>{', '.join(r.replace('_', ' ') for r in risks(a)) or 'none'}</td>"
            f"<td><span class='lane {r['lane']}'>{LANES[r['lane']]}</span><div class='why'>{html.escape(r['reason'])}</div></td>"
            f"<td class='tick'>{'✓' if r['lane_ok'] else '✗ expected ' + LANES[r['exp']['lane']]}</td></tr>"
        )

    banner = (
        "<div class='banner'>SELF-TEST: built from hand labels to check the pipeline. Not model output.</div>"
        if selftest else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Inbox triage results</title>
<style>
body{{font:14px/1.45 -apple-system,system-ui,sans-serif;background:#f6f5f2;color:#1d1d1b;margin:0;padding:32px}}
.wrap{{max-width:1100px;margin:auto}} h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b6b66;margin:0 0 20px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
.stat{{background:#fff;border:1px solid #e4e2dc;border-radius:10px;padding:14px}}
.stat b{{display:block;font-size:24px}} .stat span{{color:#6b6b66;font-size:12px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e4e2dc;border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #eeece6;vertical-align:top}}
th{{font-size:12px;color:#6b6b66;font-weight:600;background:#fbfaf7}}
td.msg{{width:42%}} .c{{color:#8a8a84;font-size:12px;margin-left:6px}} .why{{color:#6b6b66;font-size:12px;margin-top:4px}}
.lane{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}}
.human_now{{background:#fde2df;color:#a3261b}} .human_queue{{background:#fdf0cf;color:#8a5a00}}
.auto{{background:#dff3e4;color:#1d6b35}} .archive{{background:#e8e8e5;color:#555}}
.tick{{white-space:nowrap;color:#1d6b35}} tr.miss .tick{{color:#a3261b}} tr.miss{{background:#fff8f7}}
.banner{{background:#a3261b;color:#fff;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-weight:600}}
</style></head><body><div class="wrap">{banner}
<h1>20 customer emails, sorted by Jev</h1>
<p class="sub">Model {html.escape(rows[0]['model'])} · {len(QUESTIONS)} questions per email, one call each · rules in plain code decide the lane</p>
<div class="stats">
<div class="stat"><b>{human_ok}/{n}</b><span>correct call on "does a human need to see this?"</span></div>
<div class="stat"><b>{len(missed)}</b><span>urgent or risky emails sent to automation</span></div>
<div class="stat"><b>{lat[n // 2]} ms</b><span>median time per email</span></div>
<div class="stat"><b>${monthly:.3f}</b><span>per month at {MONTHLY_EMAILS} emails (≈{avg_tok:.0f} tokens each)</span></div>
</div>
<p class="sub">Human now: {counts['human_now']} · Human queue: {counts['human_queue']} · Automated: {counts['auto']} · Archived: {counts['archive']} · Exact lane match: {lane_ok}/{n}{'' if order_flips is None else f' · Topic changed when options were reversed: {order_flips}/{n}'}</p>
<table><tr><th>Email</th><th>Topic</th><th>Risk</th><th>Lane</th><th></th></tr>
{''.join(cell(r) for r in rows)}
</table></div></body></html>"""


def main() -> None:
    emails = json.loads((ROOT / "emails.json").read_text(encoding="utf-8"))

    if "--dry-run" in sys.argv:
        print(json.dumps(payload_for(emails[0]), indent=2, ensure_ascii=False))
        return

    selftest = "--selftest" in sys.argv
    key = "" if selftest else load_api_key()
    if not selftest and not key:
        raise SystemExit("No API key. Set TYPESAFE_API_KEY or save it to ~/.typesafe/api_key (chmod 600).")

    rows, raw = [], []
    for e in emails:
        if selftest:
            body, ms = {"model": "SELF-TEST", "answers": fake_answers(e["expected"]), "usage": {"input_tokens": 0}}, 0
        else:
            body, ms = call_jev(e, key)
        raw.append({"id": e["id"], "latency_ms": ms, "response": body})
        lane, reason = decide(body["answers"])
        rows.append({
            "id": e["id"], "text": e["text"], "exp": e["expected"], "answers": body["answers"],
            "model": body.get("model", MODEL), "lane": lane, "reason": reason,
            "lane_ok": ok(lane, e["expected"], "lane"),
            "tokens": body.get("usage", {}).get("input_tokens", 0), "latency_ms": ms,
        })
        print(f"{e['id']}  {LANES[lane]:<12} {'ok ' if rows[-1]['lane_ok'] else 'MISS'}  {reason}")

    order_flips = None
    if "--order-check" in sys.argv and not selftest:
        order_flips = 0
        for e, r in zip(emails, rows):
            body, _ = call_jev(e, key, reversed_topic_questions())
            if body["answers"]["topic"]["choice"] != r["answers"]["topic"]["choice"]:
                order_flips += 1
                print(f"{e['id']}  topic flipped with reversed options: {r['answers']['topic']['choice']} -> {body['answers']['topic']['choice']}")

    suffix = "-selftest" if selftest else ""
    (RESULTS / f"raw{suffix}.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    out = RESULTS / f"report{suffix}.html"
    out.write_text(build_report(rows, selftest, order_flips), encoding="utf-8")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
