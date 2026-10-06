# Jev Inbox Triage

An experiment in giving a small online store's inbox a quiet clerk: a decision model reads every customer email first and decides which ones a person needs to see now, which can wait, which a template could answer, and which are only sales pitches.

<p align="center">
  <img src="docs/preview.gif" alt="Twenty customer emails sorted into four lanes by Jev" width="400" />
</p>

| | Result |
|---|---|
| Emails sent to the right lane | **19 / 20** |
| Urgent or risky emails sent to automation | **0** |
| Median time to judge an email | **338 ms** |
| Input per email | ~1,000 tokens (8 questions, one call) |
| Cost at 600 emails a month | **~2.5 US cents** |

Model: `jev-1.13.0` from [TypeSafe](https://typesafe.ai). Real output, recorded 6 October 2026 in [`results/raw.json`](results/raw.json). The emails are a test inbox written for this experiment, not a live store.

## Why this exists

Anyone who has answered a small store's inbox knows the particular weariness of it: the same handful of letters arriving all day in no useful order, so that the parent whose child has swallowed some cleanser waits patiently behind an agency promising to triple the brand's sales. Most of those letters never needed the owner at all; a few needed them urgently, and the cost of the inbox lies almost entirely in the time it takes to tell the two apart.

Jev, a newly released "System One" model from TypeSafe, seemed built for exactly that distinction. It cannot write a sentence and does not try to; it answers narrow questions about a piece of text with typed labels and a measure of how sure it is, and leaves every consequential action to ordinary code that can be read and tested. This repository is the result of asking whether that is enough to keep the right letters in front of the right person.

## How it works

Each email is sent to Jev once, with eight questions answered in parallel:

| Question | Type |
|---|---|
| What is the email about (order, damaged item, refund, product question, other) | Choice |
| How soon does the sender need a reply | Score (3 levels) |
| How does the sender feel about the store | Score (3 levels) |
| Is anyone's health or safety involved | Noul (yes/no) |
| Is there a chargeback, dispute or legal mention | Noul |
| Is the sender threatening a public complaint | Noul |
| Is the sender asking for their money back | Noul |
| Is this a business pitching its own services | Noul |

Plain rules in [`triage.py`](triage.py) then choose a lane. Safety checks run first, and anything the model is unsure about goes to a person rather than to automation:

```
any risk flag ≥ 0.30            → Human now
sales pitch ≥ 0.80              → Archived
customer close to leaving       → Human now
model unsure                    → Human queue
order / damaged / refund topic  → Automated (would send tracking, photo request, returns form)
needs a reply today             → Human now
everything else                 → Human queue
```

The thresholds follow TypeSafe's guidance that a threshold should scale with the cost of being wrong: a missed safety issue is expensive, so risk flags trigger early; archiving a real customer is expensive, so a pitch must be clear before it is archived.

## What it got wrong, and nearly wrong

- **e12, a calm return.** Jev was 61% sure the customer wanted a refund, inside the "unsure" band, so it went to a person instead of receiving the automatic returns form. A defensible reading, and the only lane miss.
- **e04, a gift that missed a birthday.** Correct lane, partly for the wrong reason: a 0.44 "public complaint" score crossed the risk threshold although the customer never threatened to post anything.
- **e03, option-order check.** Re-asking the topic question with the options reversed changed one answer out of twenty; that email's topic confidence was already only 0.41, and it reached a person regardless.

The thresholds were set before the run and were not tuned afterwards to reach twenty out of twenty.

## Run it

```bash
python3 triage.py --dry-run       # print the request for one email, no API call
python3 triage.py --selftest      # check the pipeline using hand labels (report is stamped as not model output)
python3 triage.py                 # live run; needs a TypeSafe key
python3 triage.py --order-check   # live run, then re-ask topic with reversed options
```

Standard library only. Put a key from [console.typesafe.ai](https://console.typesafe.ai/keys) in `TYPESAFE_API_KEY` or `~/.typesafe/api_key` (mode 600). The report is written to `results/report.html`.

## n8n workflow (work in progress)

[`n8n/`](n8n/) holds a shadow-mode workflow for a real Gmail inbox: labels, a Slack alert for urgent mail and a Google Sheet log, with nothing ever sent to a customer. It is generated from `triage.py` and its rules match the Python version on all twenty emails, but it has not yet been run inside a live n8n instance. See [`n8n/README.md`](n8n/README.md).

## The video

- [`videos/inbox-triage-15s/`](videos/inbox-triage-15s/): the 15-second version, built with [HyperFrames](https://github.com/heygen-com/hyperframes). `build-data.py` inlines the real run and generates the sound cues from the same schedule as the animation. The final render is in `renders/`.
- [`animation/`](animation/): the first 32-second cut, plain HTML captured frame by frame with Playwright and ffmpeg.

## Privacy

Running this on a real inbox sends customer emails to TypeSafe. Read their data terms first, and make sure the store's privacy policy covers it.

## Credits

- [TypeSafe](https://typesafe.ai) for Jev and its documentation and agent skill.
- [Cobus Greyling's Jev showcase](https://github.com/cobusgreyling/Jev), where I first came across Jev.
- [HyperFrames](https://github.com/heygen-com/hyperframes) for the video framework.
- Sound effects from [Pixabay](https://pixabay.com/sound-effects/) under the Pixabay Content License, listed in [`videos/inbox-triage-15s/assets/sfx/CREDITS.md`](videos/inbox-triage-15s/assets/sfx/CREDITS.md).

## License

MIT. See [LICENSE](LICENSE).
