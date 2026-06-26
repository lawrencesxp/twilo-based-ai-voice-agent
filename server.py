"""
Media-stream proxy.

Twilio places the call and opens a WebSocket to /media-stream. This server bridges
that audio to the OpenAI Realtime API (speech-to-speech) and back, so OpenAI plays
the "patient" and hears the clinic's agent.

It also:
  - logs a correctly-labeled transcript (AGENT vs PATIENT) from Realtime events
  - handles barge-in (clears Twilio's buffer + truncates OpenAI when talked over)
  - hangs up when the model calls the end_call tool

Audio is g711 u-law (8kHz) on BOTH sides, so no transcoding is needed.
"""
import os
import json
import asyncio
import datetime

import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect
from twilio.rest import Client as TwilioClient

from config import (
    OPENAI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
    REALTIME_MODEL, VOICE, TRANSCRIBE_MODEL, BOT_SPEAKS_FIRST,
    TRANSCRIPTS_DIR, NGROK_DOMAIN,
)
from scenarios import get_scenario

app = FastAPI()
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

# The Realtime API renamed some events across versions. Accept both spellings so
# the proxy keeps working if OpenAI shifts naming again.
AUDIO_DELTA_EVENTS = {"response.output_audio.delta", "response.audio.delta"}
ASSISTANT_DONE_EVENTS = {"response.output_audio_transcript.done", "response.audio_transcript.done"}

END_CALL_TOOL = {
    "type": "function",
    "name": "end_call",
    "description": (
        "Hang up the phone call. This ENDS the call immediately, so only use it once the "
        "conversation is truly over. Do NOT call this in the same turn as a spoken reply, "
        "and NEVER call it right after asking a question or while either party is still "
        "mid-topic. Use it only after goodbyes have actually been exchanged: you said a "
        "goodbye AND the agent responded to it -- or the agent has clearly stated it cannot "
        "help and there is nothing left to say."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Brief reason for ending the call."}
        },
        "required": [],
    },
}


@app.get("/")
async def root():
    return {"status": "ok", "model": REALTIME_MODEL}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """Optional: lets you test by pointing your Twilio number's inbound webhook here."""
    scenario_id = request.query_params.get("scenario_id", "schedule_basic")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="wss://{NGROK_DOMAIN}/media-stream">'
        f'<Parameter name="scenario_id" value="{scenario_id}"/>'
        "</Stream></Connect></Response>"
    )
    return PlainTextResponse(content=twiml, media_type="text/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    print("Twilio connected to /media-stream")

    # Per-call state
    stream_sid = None
    call_sid = None
    scenario = None
    transcript = []            # list of (speaker, text)
    latest_media_ts = 0        # ms, from Twilio media frames
    response_start_ts = None   # ms, when current assistant turn began playing
    last_assistant_item = None # for truncation on barge-in
    should_close = asyncio.Event()

    async with websockets.connect(
        OPENAI_REALTIME_URL,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
    ) as openai_ws:

        async def configure_session(scn):
            """Send the GA-shape session.update (nested audio.input/audio.output)."""
            session_update = {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": REALTIME_MODEL,
                    "output_modalities": ["audio"],
                    "instructions": scn["system_prompt"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},  # G.711 u-law, object form
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 600,
                            },
                            "transcription": {"model": TRANSCRIBE_MODEL},
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},  # G.711 u-law, object form
                            "voice": VOICE,
                        },
                    },
                    "tools": [END_CALL_TOOL],
                    "tool_choice": "auto",
                },
            }
            await openai_ws.send(json.dumps(session_update))

        async def trigger_bot_greeting():
            """Only used when BOT_SPEAKS_FIRST is true (e.g. some smoke tests)."""
            await openai_ws.send(json.dumps({"type": "response.create"}))

        async def hang_up(reason=""):
            print(f"Ending call ({reason})")
            # Give any final goodbye audio ~1.2s to play before we cut the line.
            await asyncio.sleep(1.2)
            try:
                if call_sid:
                    twilio_client.calls(call_sid).update(status="completed")
            except Exception as e:
                print(f"hang_up error: {e}")
            should_close.set()

        def log(speaker, text):
            text = (text or "").strip()
            if text:
                transcript.append((speaker, text))
                print(f"{speaker}: {text}")

        # ---- Twilio -> OpenAI ----
        async def from_twilio():
            nonlocal stream_sid, call_sid, scenario, latest_media_ts
            try:
                while not should_close.is_set():
                    raw = await twilio_ws.receive_text()
                    data = json.loads(raw)
                    event = data.get("event")

                    if event == "start":
                        start = data["start"]
                        stream_sid = start["streamSid"]
                        call_sid = start["callSid"]
                        params = start.get("customParameters", {}) or {}
                        scenario_id = params.get("scenario_id", "schedule_basic")
                        scenario = get_scenario(scenario_id)
                        print(f"Call {call_sid} | scenario={scenario_id}")
                        await configure_session(scenario)
                        if BOT_SPEAKS_FIRST:
                            await trigger_bot_greeting()

                    elif event == "media":
                        latest_media_ts = int(data["media"]["timestamp"])
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data["media"]["payload"],
                        }))

                    elif event == "stop":
                        print("Twilio stream stopped")
                        break
            except WebSocketDisconnect:
                print("Twilio WebSocket disconnected")
            finally:
                should_close.set()

        # ---- OpenAI -> Twilio ----
        async def from_openai():
            nonlocal response_start_ts, last_assistant_item
            try:
                async for raw in openai_ws:
                    event = json.loads(raw)
                    etype = event.get("type", "")

                    if etype == "error":
                        print(f"OpenAI error event: {event.get('error')}")

                    # Assistant audio -> Twilio
                    elif etype in AUDIO_DELTA_EVENTS and event.get("delta"):
                        if response_start_ts is None:
                            response_start_ts = latest_media_ts
                        if event.get("item_id"):
                            last_assistant_item = event["item_id"]
                        await twilio_ws.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": event["delta"]},
                        }))

                    # Caller (the AGENT we're testing) started speaking -> barge-in
                    elif etype == "input_audio_buffer.speech_started":
                        if last_assistant_item is not None:
                            elapsed = max(0, latest_media_ts - (response_start_ts or latest_media_ts))
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.truncate",
                                "item_id": last_assistant_item,
                                "content_index": 0,
                                "audio_end_ms": elapsed,
                            }))
                            await twilio_ws.send_text(json.dumps({
                                "event": "clear", "streamSid": stream_sid,
                            }))
                            last_assistant_item = None
                            response_start_ts = None

                    # Transcript: the AGENT's words (input transcription)
                    elif etype == "conversation.item.input_audio_transcription.completed":
                        log("AGENT", event.get("transcript", ""))

                    # Transcript: our PATIENT bot's words (assistant output)
                    elif etype in ASSISTANT_DONE_EVENTS:
                        log("PATIENT", event.get("transcript", ""))

                    elif etype == "response.done":
                        response_start_ts = None
                        last_assistant_item = None

                    # Model decided to hang up
                    elif etype == "response.function_call_arguments.done" and event.get("name") == "end_call":
                        reason = ""
                        try:
                            reason = json.loads(event.get("arguments") or "{}").get("reason", "")
                        except Exception:
                            pass
                        await hang_up(reason or "model called end_call")
                        break

                    if should_close.is_set():
                        break
            except websockets.exceptions.ConnectionClosed:
                print("OpenAI WebSocket closed")
            finally:
                should_close.set()

        await asyncio.gather(from_twilio(), from_openai())

    # ---- Write transcript on call end ----
    if scenario and transcript:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"{scenario['label']}_{(call_sid or 'nocallsid')}.txt"
        path = os.path.join(TRANSCRIPTS_DIR, fname)
        with open(path, "w") as f:
            f.write(f"Scenario: {scenario['label']}\n")
            f.write(f"Probe: {scenario['probe']}\n")
            f.write(f"Call SID: {call_sid}\n")
            f.write(f"Saved: {ts}\n")
            f.write("=" * 60 + "\n\n")
            for speaker, text in transcript:
                f.write(f"{speaker}: {text}\n")
        print(f"Transcript written: {path}")