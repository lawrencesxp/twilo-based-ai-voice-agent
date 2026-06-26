"""
Optional backup transcription.

The server already writes correctly-labeled (AGENT/PATIENT) transcripts live from
Realtime events -- that is your primary transcript. This utility is a fallback that
transcribes the recorded .mp3 files with OpenAI, in case you want a second source
or a call's live transcript didn't save.

Usage:
  python transcribe.py                 # transcribe every mp3 in recordings/
  python transcribe.py recordings/x.mp3
"""
import os
import sys

from openai import OpenAI
from config import OPENAI_API_KEY, RECORDINGS_DIR, TRANSCRIPTS_DIR

client = OpenAI(api_key=OPENAI_API_KEY)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)


def transcribe_file(path: str):
    with open(path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",   # or "whisper-1"
            file=f,
        )
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(TRANSCRIPTS_DIR, f"{base}.whisper.txt")
    with open(out, "w") as f:
        f.write(result.text)
    print(f"Wrote {out}")


def main():
    targets = sys.argv[1:]
    if not targets:
        targets = [os.path.join(RECORDINGS_DIR, n)
                   for n in os.listdir(RECORDINGS_DIR) if n.endswith(".mp3")]
    if not targets:
        print("No mp3 files to transcribe.")
        return
    for t in targets:
        transcribe_file(t)


if __name__ == "__main__":
    main()
