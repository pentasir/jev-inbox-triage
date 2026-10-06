// Lane rules, ported line for line from triage.py decide(). Order matters:
// safety checks run before anything automated.
const RISK_AT = 0.3, PITCH_AT = 0.8, LO = 0.3, HI = 0.7, TOPIC_CONF_MIN = 0.6;
const RISK_NOULS = {"safety_issue": "health or safety", "legal_or_chargeback": "legal or chargeback", "public_complaint": "public complaint"};
const AUTO_ACTIONS = {"where_is_order": "Auto-reply with live tracking", "damaged_or_wrong": "Auto-reply asking for a photo, priority queue", "refund_return": "Auto-reply with returns form"};

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

module.exports = { decide };
