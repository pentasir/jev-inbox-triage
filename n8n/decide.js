// Lane rules, ported line for line from triage.py decide(). Order matters:
// safety checks run before anything automated.
const RISK_AT = 0.3, PITCH_AT = 0.8, LO = 0.3, HI = 0.7, TOPIC_CONF_MIN = 0.6;
const RISK_NOULS = {"safety_issue": "health or safety", "legal_or_chargeback": "legal or chargeback", "public_complaint": "public complaint"};
const AUTO_ACTIONS = {"where_is_order": "Auto-reply with live tracking", "damaged_or_wrong": "Auto-reply asking for a photo, priority queue", "refund_return": "Auto-reply with returns form"};
const TOPICS = ["where_is_order", "damaged_or_wrong", "refund_return", "product_question", "other"];

function decide(a) {
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
}

// Ported from triage.py validate(): the first unusable field, or null if decide() can read every answer.
// Without this, a missing or non-numeric value fails every comparison and can fall through to Automated.
const isObj = x => x !== null && typeof x === "object" && !Array.isArray(x);
const realIn = (v, lo, hi) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;

function validate(a) {
  if (!isObj(a)) return "no answers";
  const t = a.topic;
  if (isObj(t) && t.choice !== null && typeof t.choice === "object") return "malformed answer";
  if (!isObj(t) || typeof t.choice !== "string" || !TOPICS.includes(t.choice)) return "topic.choice";
  if (!realIn(t.confidence, 0, 1)) return "topic.confidence";
  for (const q of ["urgency", "frustration"])
    if (!isObj(a[q]) || !realIn(a[q].score, 0, 2)) return `${q}.score`;
  for (const q of [...Object.keys(RISK_NOULS), "refund_requested", "is_pitch"])
    if (!isObj(a[q]) || !realIn(a[q].noul, 0, 1)) return `${q}.noul`;
  return null;
}

// Ported from triage.py decide_safe(): anything missing or unusable goes to a person.
function decideSafe(a) {
  if (a === undefined || a === null) return ["human_queue", "Jev unavailable, left for a person"];
  const field = validate(a);
  if (field) return ["human_queue", `Invalid answer (${field}), left for a person`];
  return decide(a);
}

module.exports = { decide, validate, decideSafe };
