#!/usr/bin/env python3
"""Generate the n8n shadow-mode workflow from triage.py, so n8n runs the exact
questions, thresholds and lane rules that were tested on the 20 emails.

Writes:
  n8n/jev-inbox-triage-shadow.json   import into n8n
  n8n/decide.js                      the lane rules on their own (used by the parity test)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import triage  # noqa: E402

LO, HI = triage.NOUL_UNSURE

DECIDE_JS = f"""// Lane rules, ported line for line from triage.py decide(). Order matters:
// safety checks run before anything automated.
const RISK_AT = {triage.RISK_AT}, PITCH_AT = {triage.PITCH_AT}, LO = {LO}, HI = {HI}, TOPIC_CONF_MIN = {triage.TOPIC_CONF_MIN};
const RISK_NOULS = {json.dumps({q: l.replace("_", " ") for q, l in triage.RISK_NOULS.items()})};
const AUTO_ACTIONS = {json.dumps(triage.AUTO_ACTIONS)};

function decide(a) {{
  const topic = a.topic.choice, topicConf = a.topic.confidence;
  const pitch = a.is_pitch.noul, refund = a.refund_requested.noul;
  const frustration = a.frustration.score, urgency = a.urgency.score;
  const flagged = Object.keys(RISK_NOULS).filter(q => a[q].noul >= RISK_AT).map(q => RISK_NOULS[q]);

  if (flagged.length) return ["human_now", "Risk flagged: " + flagged.join(", ")];
  if (pitch >= PITCH_AT) return ["archive", "Sales pitch"];
  if (frustration >= 1.5) return ["human_now", "Customer close to leaving"];
  if (refund >= HI && frustration >= 0.75) return ["human_now", "Upset and asking for money back"];
  if (topicConf < TOPIC_CONF_MIN || (LO < pitch && pitch < PITCH_AT) || (LO < refund && refund < HI))
    return ["human_queue", "Model unsure, left for a person"];
  if (topic in AUTO_ACTIONS) return ["auto", AUTO_ACTIONS[topic]];
  if (urgency >= 1.5) return ["human_now", "Needs a reply today"];
  if (topic === "product_question") return ["human_queue", "Product question, reply drafted for review"];
  return ["human_queue", "Needs a person"];
}}
"""

BUILD_JS = f"""// CONFIG: describe the client's store in one sentence. Jev uses it as context.
const STORE = {json.dumps(triage.STORE)};
const MAX_CHARS = 3000; // long threads cost more and add noise; keep the newest message only

const QUESTIONS = {json.dumps(triage.QUESTIONS, indent=2)};

const e = $json;
const raw = e.text || (e.textAsHtml || e.html || "").replace(/<[^>]+>/g, " ") || e.snippet || "";
// Keep only the newest message: drop quoted history and "On ... wrote:" blocks.
const newest = raw
  .split(/\\r?\\nOn .+wrote:\\r?\\n|\\r?\\n-{{2,}} ?Original Message|\\r?\\nFrom: .+\\r?\\nSent: /)[0]
  .split("\\n").filter(line => !line.trim().startsWith(">")).join("\\n")
  .trim().slice(0, MAX_CHARS);
const sender = e.from?.value?.[0] ?? {{}};
const fromName = sender.name || sender.address || e.from?.text || "unknown";

return {{
  json: {{
    emailId: e.id,
    threadId: e.threadId,
    from: fromName,
    fromAddress: sender.address || "",
    subject: e.subject || "",
    payload: {{
      model: "jev-latest",
      state: {{ store: STORE, email: {{ from: fromName, subject: e.subject || "", text: newest }} }},
      questions: QUESTIONS,
    }},
  }},
}};
"""

DECIDE_NODE_JS = DECIDE_JS + """
const a = $json.answers;
const src = $('Build Jev request').item.json;
const [lane, reason] = decide(a);

return {
  json: {
    logged_at: new Date().toISOString(),
    lane,
    reason,
    emailId: src.emailId,
    threadId: src.threadId,
    from: src.from,
    fromAddress: src.fromAddress,
    subject: src.subject,
    gmailLink: `https://mail.google.com/mail/u/0/#all/${src.threadId}`,
    topic: a.topic.choice,
    topic_conf: a.topic.confidence,
    urgency: a.urgency.score,
    frustration: a.frustration.score,
    safety: a.safety_issue.noul,
    legal_or_chargeback: a.legal_or_chargeback.noul,
    public_complaint: a.public_complaint.noul,
    refund: a.refund_requested.noul,
    pitch: a.is_pitch.noul,
    input_tokens: $json.usage?.input_tokens ?? null,
    model: $json.model ?? null,
  },
};
"""

FAILSAFE_JS = """// Jev call failed (timeout, outage, bad key). Never drop an email: send it to a person.
const src = $('Build Jev request').item.json;
return {
  json: {
    logged_at: new Date().toISOString(),
    lane: "human_queue",
    reason: "Jev unavailable, left for a person",
    emailId: src.emailId,
    threadId: src.threadId,
    from: src.from,
    fromAddress: src.fromAddress,
    subject: src.subject,
    gmailLink: `https://mail.google.com/mail/u/0/#all/${src.threadId}`,
  },
};
"""

LANE_LABELS = {
    "human_now": "Jev/1 Human now",
    "human_queue": "Jev/2 Human queue",
    "auto": "Jev/3 Would automate",
    "archive": "Jev/4 Would archive",
}


def node(name, type_, version, pos, params, **extra):
    return {"parameters": params, "name": name, "type": type_, "typeVersion": version, "position": pos, **extra}


def label_node(lane, y):
    return node(
        f"Label: {triage.LANES[lane]}", "n8n-nodes-base.gmail", 2.1, [1560, y],
        {
            "resource": "message",
            "operation": "addLabels",
            "messageId": "={{ $json.emailId }}",
            "labelIds": [],
        },
        notes=f'Pick the Gmail label "{LANE_LABELS[lane]}" (create it in Gmail first).',
        notesInFlow=True,
    )


def main() -> None:
    nodes = [
        node("Sticky: read me", "n8n-nodes-base.stickyNote", 1, [-80, -420], {
            "content": (
                "## Jev inbox triage (shadow mode)\n"
                "Labels and alerts only. Nothing is sent to customers.\n\n"
                "1. Gmail credential on the trigger and the 4 label nodes\n"
                "2. Header Auth credential on **Ask Jev**: name `Authorization`, value `Bearer <TypeSafe key>`\n"
                "3. Create the 4 Gmail labels, pick one per label node\n"
                "4. Slack channel on **Alert: Human now**\n"
                "5. Google Sheet for the shadow report on **Log to sheet**\n"
                "6. Edit STORE in **Build Jev request** for this client"
            ),
            "height": 340, "width": 520,
        }),
        node("New email", "n8n-nodes-base.gmailTrigger", 1.2, [0, 0], {
            "pollTimes": {"item": [{"mode": "everyMinute"}]},
            "simple": False,
            "filters": {"labelIds": ["INBOX"]},
            "options": {},
        }),
        node("Build Jev request", "n8n-nodes-base.code", 2, [240, 0], {
            "mode": "runOnceForEachItem",
            "jsCode": BUILD_JS,
        }),
        node("Ask Jev", "n8n-nodes-base.httpRequest", 4.2, [480, 0], {
            "method": "POST",
            "url": "https://api.typesafe.ai/v1/systemone",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.payload) }}",
            "options": {"timeout": 15000},
        }, retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueErrorOutput"),
        node("Decide lane", "n8n-nodes-base.code", 2, [760, -100], {
            "mode": "runOnceForEachItem",
            "jsCode": DECIDE_NODE_JS,
        }),
        node("Fail safe", "n8n-nodes-base.code", 2, [760, 160], {
            "mode": "runOnceForEachItem",
            "jsCode": FAILSAFE_JS,
        }),
        node("Route by lane", "n8n-nodes-base.switch", 3.2, [1060, 0], {
            "mode": "expression",
            "numberOutputs": 4,
            "output": "={{ ['human_now', 'human_queue', 'auto', 'archive'].indexOf($json.lane) }}",
        }),
        node("Log to sheet", "n8n-nodes-base.googleSheets", 4.5, [1060, 320], {
            "operation": "append",
            "documentId": {"__rl": True, "mode": "list", "value": ""},
            "sheetName": {"__rl": True, "mode": "list", "value": ""},
            "columns": {"mappingMode": "autoMapInputData", "value": {}},
            "options": {},
        }, notes="Shadow report: one row per email with every Jev score.", notesInFlow=True),
        label_node("human_now", -260),
        label_node("human_queue", -80),
        label_node("auto", 100),
        label_node("archive", 280),
        node("Alert: Human now", "n8n-nodes-base.slack", 2.2, [1820, -260], {
            "resource": "message",
            "operation": "post",
            "select": "channel",
            "channelId": {"__rl": True, "mode": "name", "value": "#inbox-urgent"},
            "text": (
                "=:red_circle: *Human now*: {{ $('Route by lane').item.json.reason }}\n"
                "From: {{ $('Route by lane').item.json.from }} <{{ $('Route by lane').item.json.fromAddress }}>\n"
                "Subject: {{ $('Route by lane').item.json.subject }}\n"
                "<{{ $('Route by lane').item.json.gmailLink }}|Open in Gmail>"
            ),
            "otherOptions": {},
        }),
    ]

    def to(name, index=0):
        return {"node": name, "type": "main", "index": index}

    labels = [f"Label: {triage.LANES[k]}" for k in ("human_now", "human_queue", "auto", "archive")]
    connections = {
        "New email": {"main": [[to("Build Jev request")]]},
        "Build Jev request": {"main": [[to("Ask Jev")]]},
        "Ask Jev": {"main": [[to("Decide lane")], [to("Fail safe")]]},  # output 2 = error
        "Decide lane": {"main": [[to("Route by lane"), to("Log to sheet")]]},
        "Fail safe": {"main": [[to("Route by lane"), to("Log to sheet")]]},
        "Route by lane": {"main": [[to(l)] for l in labels]},
        labels[0]: {"main": [[to("Alert: Human now")]]},
    }

    workflow = {
        "name": "Jev inbox triage (shadow mode)",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
    }
    (HERE / "jev-inbox-triage-shadow.json").write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")
    (HERE / "decide.js").write_text(DECIDE_JS + "\nmodule.exports = { decide };\n", encoding="utf-8")
    print(f"Wrote {len(nodes)} nodes to n8n/jev-inbox-triage-shadow.json and n8n/decide.js")


if __name__ == "__main__":
    main()
