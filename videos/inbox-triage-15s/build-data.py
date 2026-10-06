#!/usr/bin/env python3
"""Inline the real Jev run into index.html between the DATA markers.

Lanes and tags come from the same rules triage.py and animation/build.py use,
so the video can never show a number the run did not produce.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "animation"))

import triage  # noqa: E402
from build import tags_for  # noqa: E402

HEROES = ["e09", "e17"]


def main() -> None:
    emails = {e["id"]: e for e in json.loads((ROOT / "emails.json").read_text(encoding="utf-8"))}
    raw = json.loads((ROOT / "results" / "raw.json").read_text(encoding="utf-8"))

    items = []
    for r in raw:
        e, a = emails[r["id"]], r["response"]["answers"]
        lane, reason = triage.decide(a)
        items.append({
            "id": r["id"],
            "from": "No name" if e["from"] == "unknown" else e["from"],
            "text": e["text"],
            "tags": tags_for(a),
            "lane": lane,
            "reason": reason,
            "ok": triage.ok(lane, e["expected"], "lane"),
            "ms": r["latency_ms"],
        })
    items.sort(key=lambda i: (HEROES.index(i["id"]) if i["id"] in HEROES else len(HEROES), i["id"]))

    tokens = [r["response"]["usage"]["input_tokens"] for r in raw]
    stats = {
        "n": len(items),
        "correct": sum(i["ok"] for i in items),
        "risky_automated": sum(
            1 for i in items
            if emails[i["id"]]["expected"]["lane"] == "human_now" and not i["lane"].startswith("human")
        ),
        "median_ms": round(statistics.median(r["latency_ms"] for r in raw)),
        "monthly_cents": round(statistics.mean(tokens) * triage.MONTHLY_EMAILS * triage.PRICE_PER_MTOK / 1e6 * 100, 1),
        "monthly_emails": triage.MONTHLY_EMAILS,
        "model": raw[0]["response"]["model"],
    }
    data = json.dumps({"items": items, "stats": stats, "lanes": triage.LANES}, ensure_ascii=False)

    index = HERE / "index.html"
    html = index.read_text(encoding="utf-8")
    new, n = re.subn(r"/\*DATA\*/.*?/\*END-DATA\*/", lambda _: f"/*DATA*/{data}/*END-DATA*/", html, flags=re.S)
    if n != 1:
        raise SystemExit("DATA markers not found in index.html")
    cues = sfx_cues(items)
    new, n = re.subn(r"<!--SFX-->.*?<!--END-SFX-->", lambda _: "<!--SFX-->\n" + cues + "\n      <!--END-SFX-->", new, flags=re.S)
    if n != 1:
        raise SystemExit("SFX markers not found in index.html")
    index.write_text(new, encoding="utf-8")
    print(f"Inlined {len(items)} emails into {index.name} ({stats['correct']}/{stats['n']} correct), {cues.count('<audio')} sound cues")


# Mirrors the schedule constants in index.html (HERO, Q0, QSTEP, T, C0, CD). Change both together.
HERO = [(0.1, 2.6), (2.95, 2.3)]
Q0, QSTEP = 5.75, 0.2
FOCUS_T = 10.05
COUNT_LAND = FOCUS_T + 0.4 + 1.4
SFX_LEN = {"notification": 2.4, "click-soft": 0.36, "ping": 1.3, "pop": 0.7, "whoosh-short": 0.56, "whoosh": 0.56, "chime": 2.4}
# Leading silence measured with ffmpeg silencedetect (-40 dB). Trimmed via data-media-start so
# each sound is audible on its cue frame instead of arriving late.
SFX_TRIM = {"notification": 0.08, "click-soft": 0.04, "ping": 0.31, "pop": 0.04, "chime": 0.41, "whoosh-short": 0.0, "whoosh": 0.0}
# Whooshes are aligned by their loudest point (0.16 s in) to the fastest part of the move.
WHOOSH_PEAK = 0.16


def sfx_cues(items: list[dict]) -> str:
    cues: list[tuple[float, str, float]] = []
    for (enter, dwell), it in zip(HERO, items[:2]):
        cues.append((enter, "notification", 0.5))
        for j in range(len(it["tags"])):
            cues.append((enter + 0.65 + j * 0.18, "click-soft", 0.35))
        v_at = enter + 0.65 + len(it["tags"]) * 0.18 + 0.15
        cues.append((v_at + 0.35, "ping" if it["lane"] == "human_now" else "pop", 0.5))
        cues.append((enter + dwell + 0.22 - WHOOSH_PEAK, "whoosh-short", 0.3))  # card move is 0.5 s, power3.inOut
    for i, _ in enumerate(items[2:]):
        land = Q0 + i * QSTEP + (QSTEP - 0.03) + 0.25
        cues.append((land - 0.12, "click-soft", 0.22))
    cues.append((FOCUS_T + 0.25 - WHOOSH_PEAK, "whoosh", 0.45))  # peak on the crossfade midpoint
    cues.append((COUNT_LAND - 0.1, "chime", 1.3))  # soft attack: needs the boost to read
    cues.sort()
    # Give each cue the first track that is free at its start, so no two sounds share a track.
    track_end: list[float] = []
    lines = []
    for i, (t, name, vol) in enumerate(cues):
        track = next((k for k, end in enumerate(track_end) if end <= t), None)
        if track is None:
            track_end.append(0.0)
            track = len(track_end) - 1
        track_end[track] = t + SFX_LEN[name] - SFX_TRIM[name]
        lines.append(
            f'      <audio id="sfx-{i:02d}" src="assets/sfx/{name}.mp3" data-start="{t:.2f}" '
            f'data-media-start="{SFX_TRIM[name]}" data-duration="{round(SFX_LEN[name] - SFX_TRIM[name], 2)}" '
            f'data-track-index="{10 + track}" data-volume="{vol}"></audio>'
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
