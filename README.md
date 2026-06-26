# Voice Bot — Patient Simulator

An automated voice bot that **calls the Pretty Good AI test line, role-plays a realistic
patient, and stress-tests the agent** to surface bugs. It places real outbound phone calls,
holds a natural spoken conversation, records both sides, and logs a labeled transcript. Findings
are written up in [`BUGS.md`](BUGS.md).

- **Telephony:** Twilio Programmable Voice + Media Streams (outbound call + dual-channel recording)
- **Patient brain + voice:** OpenAI Realtime API (`gpt-realtime-mini`, speech-to-speech)
- **Transcripts:** logged live from Realtime events, labeled AGENT vs PATIENT
- **Server:** Python + FastAPI proxy; calls driven by a small runner script

---

## Architecture (how it works / why)

When `run_calls.py` places an outbound call, Twilio dials the clinic line and opens a **Media
Streams** WebSocket to `server.py`. The server bridges that call audio to the **OpenAI Realtime
API**, which voices the "patient" and listens to the clinic's agent — both legs run g711 µ-law
(8 kHz), so there is no transcoding and latency stays low. A **speech-to-speech** model was
chosen deliberately: the assessment rejects submissions whose voice conversation isn't lucid, and
a single realtime model handles voice activity detection, turn-taking, barge-in, and pacing far
more reliably than a stitched STT→LLM→TTS pipeline would.

Each call carries a **scenario** (persona + goal + the bug it probes) passed as a Twilio Stream
parameter; the bot pursues that goal like a real caller and deliberately does not correct the
agent's mistakes, since catching them is the point. Barge-in is handled by clearing Twilio's
buffer and truncating the model's in-flight response when the agent starts talking, and VAD
silence is tuned (1200 ms) so the bot doesn't jump into the agent's natural pauses. The bot ends
the call itself via an `end_call` tool (gated behind a real goodbye exchange), with a Twilio
`time_limit` as a hard backstop. Transcripts are logged live from Realtime events so AGENT vs
PATIENT labels are exact; Twilio dual-channel recording provides the `.mp3` audio.

---

## Setup

```bash
git clone <your-repo-url>
cd pgai-voicebot
python -m venv venv
# Windows:  venv\Scripts\Activate.ps1
# macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your values (Windows: copy .env.example .env)
```

Fill `.env` with your OpenAI key, Twilio SID/token/number, ngrok domain, and your cell for the
smoke test (see `.env.example`). You need a **paid** Twilio account (trial accounts can't dial
arbitrary numbers and inject a spoken preamble that ruins recordings) and a **funded** OpenAI
account.

---

## Run

Two background processes, then place calls.

**Terminal 1 — the proxy server:**
```bash
python -m uvicorn server:app --port 5050
```

**Terminal 2 — expose it with ngrok (use your own domain):**
```bash
ngrok http --url=https://YOUR-DOMAIN.ngrok-free.dev 5050
```
Sanity check: opening `https://YOUR-DOMAIN.ngrok-free.dev/` should return `{"status":"ok",...}`.

**Terminal 3 — place calls:**
```bash
python run_calls.py list                  # show all scenarios
python run_calls.py smoke appointment_simple   # call YOUR cell first to test the pipe
python run_calls.py call  appointment_simple   # one real call to the PGAI line
python run_calls.py all                        # run every scenario
```

Recordings are saved to `recordings/*.mp3`, transcripts to `transcripts/*.txt`.

> **Note:** `server.py` and `scenarios.py` are read once at server start. After editing either,
> restart Terminal 1 for the change to take effect.

---

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI Realtime + transcription |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio auth |
| `TWILIO_FROM_NUMBER` | your Twilio number (E.164) — the single number used for all calls |
| `NGROK_DOMAIN` | your ngrok domain (no scheme) |
| `SMOKE_TEST_NUMBER` | your cell (E.164), used only by `run_calls.py smoke` |
| `REALTIME_MODEL` | default `gpt-realtime-mini` (set `gpt-realtime-2` for higher quality) |
| `MAX_CALL_SECONDS` | hard per-call cap (backstop) |

The bot can only dial an **allow-list** (`config.py`): the PGAI test line `+18054398008` and your
smoke-test number. It cannot dial anything else.

---

## Submitting (checklist)

- [ ] ≥10 clean, lucid recordings (`recordings/*.mp3`) + matching transcripts
- [ ] `BUGS.md` filled in with confirmed timestamps
- [ ] `recordings/` and `transcripts/` committed (remove those lines from `.gitignore`, or
      `git add -f recordings/ transcripts/`)
- [ ] **No secrets committed** — `.env` is git-ignored; confirm before pushing
- [ ] **Rotate** Twilio token, ngrok token, and OpenAI key before the repo goes public
- [ ] Loom walkthrough (≤5 min) + AI-debugging screen recording (≤5 min)
- [ ] Submission form: repo link, Loom link, and the single calling number in E.164

---

## Troubleshooting

The Realtime API's wire format shifts between versions. Symptoms and fixes:

- **Session rejected / no audio:** audio format must be the object form `{"type": "audio/pcmu"}`
  (not the bare string) in `server.py`'s `configure_session`.
- **Bot talks over the agent's greeting:** increase `silence_duration_ms` in `turn_detection`
  (currently 1200).
- **Recording 404 right after a call:** Twilio needs a few seconds to finish encoding the mp3;
  `run_calls.py` retries until it's ready.
- **`KeyError: Unknown scenario_id`:** you edited `scenarios.py` but didn't restart the server.