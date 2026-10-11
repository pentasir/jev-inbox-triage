#!/usr/bin/env python3
"""Check the n8n lane rules match triage.py, including the fail-safe cases.

Runs every case through three things and compares the (lane, reason) they return:
  1. triage.decide_safe() in Python
  2. decideSafe() from n8n/decide.js
  3. the Decide lane node's own code, pulled out of the workflow JSON and run with stubbed n8n globals

Cases: the 20 recorded answers in results/raw.json, plus broken answers (missing fields, wrong types,
out of range values, no answers at all). Needs node on PATH.

  python3 n8n/parity_test.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True

import triage  # noqa: E402

NODE_RUNNER = r"""
const fs = require("fs");
const { decideSafe } = require(process.argv[1]);
const wf = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const code = wf.nodes.find(n => n.name === "Decide lane").parameters.jsCode;
const src = { emailId: "x", threadId: "t", from: "f", fromAddress: "a", subject: "s" };
const node = new Function("$json", "$", code);
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map(c => {
  const body = c.body === null ? {} : c.body;  // HTTP error path never reaches Decide lane; null body = no answers
  const row = node(body, () => ({ item: { json: src } })).json;
  return { lib: decideSafe(body.answers), node: [row.lane, row.reason], topic: row.topic ?? null };
});
process.stdout.write(JSON.stringify(out));
"""


def broken_cases(base: dict) -> dict:
    def m(path, value):
        a = copy.deepcopy(base)
        d = a
        for k in path[:-1]:
            d = d[k]
        if value is KeyError:
            del d[path[-1]]
        else:
            d[path[-1]] = value
        return {"answers": a}

    return {
        "no answers key": {"model": "jev-1.13.0"},
        "answers null": {"answers": None},
        "answers is a list": {"answers": []},
        "answers is a string": {"answers": "oops"},
        "topic.confidence missing": m(["topic", "confidence"], KeyError),
        "topic.confidence null": m(["topic", "confidence"], None),
        "topic.confidence 7": m(["topic", "confidence"], 7),
        "topic.confidence '0.9'": m(["topic", "confidence"], "0.9"),
        "topic.choice unknown": m(["topic", "choice"], "shipping"),
        "topic.choice list": m(["topic", "choice"], ["where_is_order"]),
        "topic missing": m(["topic"], KeyError),
        "safety noul missing": m(["safety_issue", "noul"], KeyError),
        "safety noul '0.5'": m(["safety_issue", "noul"], "0.5"),
        "safety noul 'high'": m(["safety_issue", "noul"], "high"),
        "safety noul true": m(["safety_issue", "noul"], True),
        "safety noul 5": m(["safety_issue", "noul"], 5),
        "safety question missing": m(["safety_issue"], KeyError),
        "pitch noul -0.1": m(["is_pitch", "noul"], -0.1),
        "urgency score -1": m(["urgency", "score"], -1),
        "frustration score 2.5": m(["frustration", "score"], 2.5),
        "urgency is a list": m(["urgency"], []),
    }


def main() -> int:
    raw = json.loads((HERE.parent / "results" / "raw.json").read_text(encoding="utf-8"))
    cases = {r["id"]: r["response"] for r in raw}
    cases.update(broken_cases(raw[0]["response"]["answers"]))

    names = list(cases)
    proc = subprocess.run(
        ["node", "-e", NODE_RUNNER, str(HERE / "decide.js"), str(HERE / "jev-inbox-triage-shadow.json")],
        input=json.dumps([{"body": cases[n]} for n in names]), capture_output=True, text=True,
    )
    if proc.returncode:
        print(proc.stderr, file=sys.stderr)
        return 1

    fails = 0
    for name, js in zip(names, json.loads(proc.stdout)):
        body = cases[name]
        py = list(triage.decide_safe(body.get("answers") if isinstance(body, dict) else None))
        good = py == js["lib"] == js["node"]
        fails += not good
        print(f"{'ok  ' if good else 'FAIL'} {name:<26} {triage.LANES[py[0]]:<12} {py[1]}")
        if not good:
            print(f"     python={py} decide.js={js['lib']} node={js['node']}")
    auto_on_bad = [n for n in names if n not in {r['id'] for r in raw} and triage.decide_safe(
        cases[n].get("answers"))[0] != "human_queue"]
    print(f"\n{len(names) - fails}/{len(names)} match across Python, decide.js and the Decide lane node")
    print(f"Broken cases outside Human queue: {len(auto_on_bad)}")
    return 1 if fails or auto_on_bad else 0


if __name__ == "__main__":
    sys.exit(main())
