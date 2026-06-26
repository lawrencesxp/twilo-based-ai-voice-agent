"""
Outbound call runner.

Usage:
  python run_calls.py list                 # show all scenarios
  python run_calls.py smoke <scenario_id>  # call YOUR cell (SMOKE_TEST_NUMBER) to test the pipe
  python run_calls.py call  <scenario_id>  # one real call to the PGAI test line
  python run_calls.py all                  # run every scenario against the PGAI test line

The media-stream server (server.py) must already be running and exposed via ngrok
at NGROK_DOMAIN before you place calls.
"""
import os
import sys
import time

import requests
from twilio.rest import Client

from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
    NGROK_DOMAIN, ALLOWED_NUMBERS, PGAI_TEST_LINE, SMOKE_TEST_NUMBER,
    RECORDINGS_DIR, MAX_CALL_SECONDS,
)
from scenarios import SCENARIOS, get_scenario

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
os.makedirs(RECORDINGS_DIR, exist_ok=True)

DONE_STATES = {"completed", "failed", "busy", "no-answer", "canceled"}


def build_twiml(scenario_id: str) -> str:
    ws_url = f"wss://{NGROK_DOMAIN}/media-stream"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}">'
        f'<Parameter name="scenario_id" value="{scenario_id}"/>'
        "</Stream></Connect></Response>"
    )


def place_call(to_number: str, scenario_id: str) -> str:
    if to_number not in ALLOWED_NUMBERS:
        raise SystemExit(f"REFUSING to call {to_number}: not in allow-list {ALLOWED_NUMBERS}")
    get_scenario(scenario_id)  # validate the id early
    call = client.calls.create(
        to=to_number,
        from_=TWILIO_FROM_NUMBER,
        twiml=build_twiml(scenario_id),
        record=True,
        recording_channels="dual",   # caller and agent on separate stereo channels
        time_limit=MAX_CALL_SECONDS,  # hard backstop so nothing runs forever
    )
    print(f"Placed call {call.sid}  ->  {to_number}  (scenario={scenario_id})")
    return call.sid


def wait_for_completion(call_sid: str, timeout: int = MAX_CALL_SECONDS + 120) -> str:
    start = time.time()
    while time.time() - start < timeout:
        status = client.calls(call_sid).fetch().status
        if status in DONE_STATES:
            print(f"Call {call_sid} finished: {status}")
            return status
        time.sleep(3)
    print(f"Call {call_sid} timed out waiting for completion")
    return "timeout"


def fetch_recording(call_sid: str, scenario_id: str, attempts: int = 20) -> str | None:
    """Recordings appear a few seconds after a call ends, and the .mp3 takes a
    little longer to finish encoding. Poll until the recording exists AND its
    media downloads successfully (Twilio 404s the .mp3 until it's ready)."""
    scn = get_scenario(scenario_id)
    for i in range(attempts):
        recs = client.recordings.list(call_sid=call_sid, limit=1)
        if recs:
            rec = recs[0]
            # Only try to download once Twilio marks it completed.
            if getattr(rec, "status", "completed") == "completed":
                url = (f"https://api.twilio.com/2010-04-01/Accounts/"
                       f"{TWILIO_ACCOUNT_SID}/Recordings/{rec.sid}.mp3")
                resp = requests.get(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
                if resp.status_code == 200 and resp.content:
                    out = os.path.join(RECORDINGS_DIR, f"{scn['label']}_{call_sid}.mp3")
                    with open(out, "wb") as f:
                        f.write(resp.content)
                    print(f"Saved recording: {out}")
                    return out
                # 404 / not ready yet -> wait and retry
        print(f"  recording not ready yet ({i + 1}/{attempts})...")
        time.sleep(3)
    print(f"No recording found for {call_sid} (check Twilio console > Monitor > Recordings).")
    return None


def run_one(to_number: str, scenario_id: str):
    sid = place_call(to_number, scenario_id)
    wait_for_completion(sid)
    fetch_recording(sid, scenario_id)


def cmd_list():
    print("Available scenarios:\n")
    for s in SCENARIOS:
        print(f"  {s['id']:<18} {s['probe']}")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
        return

    cmd = args[0]

    if cmd == "smoke":
        if not SMOKE_TEST_NUMBER:
            raise SystemExit("Set SMOKE_TEST_NUMBER in your .env first (your cell, E.164).")
        scenario_id = args[1] if len(args) > 1 else "schedule_basic"
        run_one(SMOKE_TEST_NUMBER, scenario_id)

    elif cmd == "call":
        if len(args) < 2:
            raise SystemExit("Usage: python run_calls.py call <scenario_id>")
        run_one(PGAI_TEST_LINE, args[1])

    elif cmd == "all":
        for i, s in enumerate(SCENARIOS, 1):
            print(f"\n===== [{i}/{len(SCENARIOS)}] {s['id']} =====")
            try:
                run_one(PGAI_TEST_LINE, s["id"])
            except Exception as e:
                print(f"Scenario {s['id']} errored: {e}")
            time.sleep(5)  # breathe between calls

    else:
        raise SystemExit(f"Unknown command '{cmd}'. Use: list | smoke | call | all")


if __name__ == "__main__":
    main()