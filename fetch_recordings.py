"""Download the Twilio trunk recording (both sides, dual-channel) for each call as MP3.

Matches by the Twilio Call SID LiveKit exposes on the SIP participant; falls
back to the recording whose start time is closest to when the call was answered.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()
CALLS_DIR = Path(os.getenv("CALLS_DIR", "calls"))
SID, TOKEN = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
client = Client(SID, TOKEN)


def find_recording(meta: dict):
    if meta.get("twilio_call_sid"):
        recs = client.recordings.list(call_sid=meta["twilio_call_sid"], limit=5)
        if recs:
            return recs[0]
    if not meta.get("answered_at"):
        return None
    answered = datetime.fromisoformat(meta["answered_at"])
    recs = client.recordings.list(date_created_after=answered - timedelta(minutes=2), limit=50)
    recs = [r for r in recs if r.start_time]
    return min(recs, key=lambda r: abs((r.start_time - answered).total_seconds()), default=None)


def main() -> None:
    for meta_path in sorted(CALLS_DIR.glob("*/meta.json")):
        out = meta_path.parent / "recording.mp3"
        if out.exists():
            continue
        rec = find_recording(json.loads(meta_path.read_text()))
        if not rec:
            print(f"no recording yet for {meta_path.parent.name}")
            continue
        url = f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Recordings/{rec.sid}.mp3?RequestedChannels=2"
        resp = requests.get(url, auth=(SID, TOKEN), timeout=60)
        resp.raise_for_status()
        out.write_bytes(resp.content)
        print(f"saved {out}")


if __name__ == "__main__":
    main()
