#!/usr/bin/env python3
"""Build animation/index.html from the real Jev run (results/raw.json).

Every tag, number and lane in the animation comes from the recorded answers,
passed through the same rules triage.py uses.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import triage  # noqa: E402

HEROES = ["e09", "e17", "e01", "e14"]  # shown slowly first: one per lane
TOPIC_LABELS = {
    "where_is_order": "where's my order",
    "damaged_or_wrong": "damaged or wrong",
    "refund_return": "refund or return",
    "product_question": "product question",
    "other": "other",
}


def tags_for(a: dict) -> list[dict]:
    tags = [{"k": "topic", "t": f"{TOPIC_LABELS[a['topic']['choice']]}  {a['topic']['confidence']:.2f}"}]
    for q, label in (("safety_issue", "safety"), ("legal_or_chargeback", "chargeback"), ("public_complaint", "public complaint")):
        if a[q]["noul"] >= triage.RISK_AT:
            tags.append({"k": "risk", "t": f"{label}  {a[q]['noul']:.2f}"})
    if a["is_pitch"]["noul"] >= 0.3:
        tags.append({"k": "pitch", "t": f"sales pitch  {a['is_pitch']['noul']:.2f}"})
    if a["refund_requested"]["noul"] >= 0.3:
        tags.append({"k": "refund", "t": f"wants refund  {a['refund_requested']['noul']:.2f}"})
    if a["frustration"]["score"] >= 1.5:
        tags.append({"k": "risk", "t": "about to leave"})
    if a["urgency"]["score"] >= 1.5:
        tags.append({"k": "refund", "t": "reply today"})
    return tags


def main() -> None:
    emails = {e["id"]: e for e in json.loads((ROOT / "emails.json").read_text(encoding="utf-8"))}
    raw = json.loads((ROOT / "results" / "raw.json").read_text(encoding="utf-8"))

    items = []
    for r in raw:
        e = emails[r["id"]]
        a = r["response"]["answers"]
        lane, reason = triage.decide(a)
        items.append({
            "id": r["id"], "from": "No name" if e["from"] == "unknown" else e["from"], "text": e["text"], "tags": tags_for(a),
            "lane": lane, "reason": reason, "ok": triage.ok(lane, e["expected"], "lane"),
            "ms": r["latency_ms"],
        })
    items.sort(key=lambda i: (HEROES.index(i["id"]) if i["id"] in HEROES else len(HEROES), i["id"]))

    tokens = [r["response"]["usage"]["input_tokens"] for r in raw]
    risky_automated = sum(
        1 for i in items if emails[i["id"]]["expected"]["lane"] == "human_now" and not i["lane"].startswith("human")
    )
    stats = {
        "n": len(items),
        "correct": sum(i["ok"] for i in items),
        "risky_automated": risky_automated,
        "median_ms": round(statistics.median(r["latency_ms"] for r in raw)),
        "monthly_cents": round(statistics.mean(tokens) * triage.MONTHLY_EMAILS * triage.PRICE_PER_MTOK / 1e6 * 100, 1),
        "monthly_emails": triage.MONTHLY_EMAILS,
        "model": raw[0]["response"]["model"],
    }
    data = {"items": items, "stats": stats, "lanes": triage.LANES}

    html = (HERE / "template.html").read_text(encoding="utf-8")
    out = HERE / "index.html"
    out.write_text(html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    print(f"Built {out} ({len(items)} emails, {stats['correct']}/{stats['n']} correct)")


if __name__ == "__main__":
    main()
