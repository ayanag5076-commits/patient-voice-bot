"""One-command runner: starts the worker, places calls one at a time, then
fetches recordings and drafts bug candidates.

    python run.py --all
    python run.py --scenario weekend_booking --scenario barge_in
    python run.py --all --no-worker      # if you already run `python agent.py start`
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid

from dotenv import load_dotenv
from livekit import api

from agent import AGENT_NAME, ALLOWED_NUMBERS, TARGET_NUMBER
from scenarios import SCENARIOS

load_dotenv()
CALL_TIMEOUT_S = 420


async def place_call(lk: api.LiveKitAPI, scenario_id: str) -> None:
    room = f"call-{scenario_id}-{uuid.uuid4().hex[:6]}"
    print(f"\n>>> {scenario_id}  (room {room})")
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME, room=room,
            metadata=json.dumps({"scenario": scenario_id, "phone": TARGET_NUMBER}),
        )
    )
    # Wait until the room has existed and then gone away (call ended).
    seen, start = False, time.time()
    while time.time() - start < CALL_TIMEOUT_S:
        await asyncio.sleep(5)
        rooms = (await lk.room.list_rooms(api.ListRoomsRequest(names=[room]))).rooms
        if rooms:
            seen = True
        elif seen:
            print(f"<<< {scenario_id} finished in {int(time.time() - start)}s")
            return
    print(f"!!! {scenario_id} timed out; deleting room")
    await lk.room.delete_room(api.DeleteRoomRequest(room=room))


async def main(ids: list[str], gap: int) -> None:
    lk = api.LiveKitAPI()
    try:
        for i, sid in enumerate(ids):
            await place_call(lk, sid)
            if i < len(ids) - 1:
                await asyncio.sleep(gap)  # let the line reset between calls
    finally:
        await lk.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", action="append", default=[], choices=list(SCENARIOS))
    p.add_argument("--all", action="store_true")
    p.add_argument("--gap", type=int, default=15, help="seconds between calls")
    p.add_argument("--no-worker", action="store_true")
    p.add_argument("--skip-post", action="store_true", help="skip recording fetch + analysis")
    args = p.parse_args()
    ids = list(SCENARIOS) if args.all else args.scenario
    if not ids:
        p.error("pass --all or at least one --scenario")
    if not TARGET_NUMBER or TARGET_NUMBER not in ALLOWED_NUMBERS:
        p.error("set TARGET_NUMBER in .env and add it to ALLOWED_NUMBERS")

    worker = None
    if not args.no_worker:
        worker = subprocess.Popen([sys.executable, "agent.py", "start"])
        time.sleep(8)  # give the worker time to register
    try:
        asyncio.run(main(ids, args.gap))
    finally:
        if worker:
            worker.terminate()

    if not args.skip_post:
        time.sleep(60)  # Twilio needs a minute to finalize the last recording
        subprocess.run([sys.executable, "fetch_recordings.py"])
        subprocess.run([sys.executable, "transcribe_calls.py"])
        subprocess.run([sys.executable, "analyze.py"])
