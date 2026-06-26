"""Central configuration. All secrets come from the local .env file (never committed)."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Secrets (loaded from .env) ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]   # your purchased Twilio number, E.164
NGROK_DOMAIN = os.environ["NGROK_DOMAIN"]               # e.g. plausibly-semicolon-sedation.ngrok-free.dev

# Your own cell, E.164, used ONLY for the smoke test. Optional.
SMOKE_TEST_NUMBER = os.environ.get("SMOKE_TEST_NUMBER", "").strip()

# --- Hard dial allow-list -------------------------------------------------
# The bot can physically only dial numbers in this set. This is the guardrail
# the assessment hints at ("calls ONLY our test number").
PGAI_TEST_LINE = "+18054398008"
ALLOWED_NUMBERS = {PGAI_TEST_LINE}
if SMOKE_TEST_NUMBER:
    ALLOWED_NUMBERS.add(SMOKE_TEST_NUMBER)

# --- Models / voice -------------------------------------------------------
# gpt-realtime-mini keeps cost low; the patient role doesn't need flagship reasoning.
# Swap to "gpt-realtime-2" if you want max quality (and higher cost).
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-mini")
VOICE = os.environ.get("VOICE", "alloy")  # alloy, ash, ballad, coral, echo, sage, shimmer, verse
# Transcription model for logging the AGENT's side of the call (their words).
# gpt-4o-mini-transcribe is cheap and reliable; gpt-realtime-whisper also works.
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

# Let the bot greet first? Keep FALSE for the real test line so THEIR agent
# greets and our bot responds like a real caller would.
BOT_SPEAKS_FIRST = os.environ.get("BOT_SPEAKS_FIRST", "false").lower() == "true"

# --- Runtime --------------------------------------------------------------
PORT = int(os.environ.get("PORT", "5050"))
MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "360"))  # Twilio hard cutoff per call
RECORDINGS_DIR = "recordings"
TRANSCRIPTS_DIR = "transcripts"
