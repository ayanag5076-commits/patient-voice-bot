"""Patient-simulator voice bot.

A LiveKit Agents worker that dials a clinic phone line over SIP and plays a
patient, using a classic pipeline: Deepgram STT -> OpenAI LLM -> Cartesia TTS,
with Silero VAD + LiveKit's turn-detector model deciding when it's our turn.

Run the worker:   python agent.py start
Place calls:      python run.py --all        (see README)
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from scenarios import SCENARIOS, build_instructions

load_dotenv()
log = logging.getLogger("patient-bot")

# Safety rail: the bot only dials TARGET_NUMBER, and only if it is also listed in
# ALLOWED_NUMBERS. Point it at a line you own or have written permission to test.
TARGET_NUMBER = os.getenv("TARGET_NUMBER", "").strip()
ALLOWED_NUMBERS = {n.strip() for n in os.getenv("ALLOWED_NUMBERS", "").split(",") if n.strip()}
AGENT_NAME = "patient-caller"
CLINIC_IDENTITY = "clinic-agent"
CALLS_DIR = Path(os.getenv("CALLS_DIR", "calls"))
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "240"))
SILENCE_NUDGE_SECONDS = 8


class Transcript:
    """Collects both sides of the call with offsets from the moment the call is answered."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.answered_at: str | None = None
        self.turns: list[dict] = []

    def mark_answered(self) -> None:
        self.t0 = time.monotonic()
        self.answered_at = datetime.now(timezone.utc).isoformat()

    def add(self, role: str, text: str, interrupted: bool = False) -> None:
        # From our bot's point of view the remote party (the clinic's AI) is the "user".
        speaker = "PATIENT (bot)" if role == "assistant" else "CLINIC AGENT"
        self.turns.append(
            {"t": round(time.monotonic() - self.t0, 1), "speaker": speaker,
             "text": text.strip(), "interrupted": interrupted}
        )

    def to_text(self) -> str:
        lines = []
        for turn in self.turns:
            mm, ss = divmod(int(turn["t"]), 60)
            cut = " [interrupted]" if turn["interrupted"] else ""
            lines.append(f"[{mm:02d}:{ss:02d}] {turn['speaker']}: {turn['text']}{cut}")
        return "\n".join(lines) + "\n"


async def hangup() -> None:
    job = get_job_context()
    await job.api.room.delete_room(api.DeleteRoomRequest(room=job.room.name))


class Patient(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)
        self.started = time.monotonic()

    @function_tool
    async def end_call(self, ctx: RunContext) -> None:
        """Hang up the phone. Call this only in the same turn where you say goodbye, after the office has wrapped up."""
        said = [i.text_content.lower() for i in self.chat_ctx.items
                if getattr(i, "role", None) in ("user", "assistant") and i.text_content]
        last = " ".join(said[-2:])
        if time.monotonic() - self.started < 45 or "bye" not in last:
            log.info("ignored early end_call (last lines: %r)", last[-80:])
            return "Not hung up. The call is still connected."
        await ctx.wait_for_playout()  # let our goodbye finish playing first
        await hangup()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    phone = meta.get("phone", TARGET_NUMBER)
    if not phone or phone != TARGET_NUMBER or phone not in ALLOWED_NUMBERS:
        raise RuntimeError("Refusing to dial: set TARGET_NUMBER and list it in ALLOWED_NUMBERS in .env.")
    scenario = SCENARIOS[meta.get("scenario", "new_patient_scheduling")]

    await ctx.connect()

    call_dir = CALLS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{scenario['id']}"
    call_dir.mkdir(parents=True, exist_ok=True)
    transcript = Transcript()
    call_meta: dict = {"scenario": scenario["id"], "goal": scenario["goal"], "room": ctx.room.name}

    tts_kwargs = {"model": "sonic-2"}
    if voice := scenario.get("voice") or os.getenv("CARTESIA_VOICE_ID"):
        tts_kwargs["voice"] = voice

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en-US"),
        llm=openai.LLM(model=os.getenv("LLM_MODEL", "gpt-6-luna"), temperature=0.7, reasoning_effort="none"),
        tts=cartesia.TTS(**tts_kwargs),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        # How long to wait after the clinic agent stops before we answer.
        # The barge-in scenario lowers this on purpose.
        min_endpointing_delay=scenario.get("min_endpointing_delay", 1.2),
        max_endpointing_delay=3.0,
        allow_interruptions=True,
        preemptive_generation=True,
    )

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) in ("user", "assistant") and item.text_content:
            transcript.add(item.role, item.text_content, getattr(item, "interrupted", False))

    @session.on("function_tools_executed")
    def _no_reply_after_end_call(ev):
        # The result stays in history, but there's no follow-up generation, so no repeat
        if any(c.name == "end_call" for c in ev.function_calls):
            ev.cancel_tool_reply()

    async def write_outputs() -> None:
        call_meta["answered_at"] = transcript.answered_at
        call_meta["ended_at"] = datetime.now(timezone.utc).isoformat()
        call_meta["duration_s"] = round(time.monotonic() - transcript.t0, 1)
        (call_dir / "session_transcript.txt").write_text(transcript.to_text())
        (call_dir / "session_transcript.json").write_text(json.dumps(transcript.turns, indent=2))
        (call_dir / "meta.json").write_text(json.dumps(call_meta, indent=2))
        log.info("saved call to %s", call_dir)

    ctx.add_shutdown_callback(write_outputs)

    # Start the pipeline first so it's listening the instant the call connects.
    # The patient never speaks first: the clinic agent greets, we respond.
    session_task = asyncio.create_task(
        session.start(
            agent=Patient(build_instructions(scenario)),
            room=ctx.room,
            room_input_options=RoomInputOptions(participant_identity=CLINIC_IDENTITY),
        )
    )

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=os.environ["SIP_OUTBOUND_TRUNK_ID"],
                sip_call_to=phone,
                participant_identity=CLINIC_IDENTITY,
                participant_name="Clinic agent",
                wait_until_answered=True,
            )
        )
    except api.TwirpError as e:
        log.error("call failed: %s (SIP %s)", e.message, e.metadata.get("sip_status_code"))
        call_meta["error"] = e.message
        ctx.shutdown()
        return

    await session_task
    participant = await ctx.wait_for_participant(identity=CLINIC_IDENTITY)
    transcript.mark_answered()
    # Used later to match the Twilio trunk recording to this call.
    call_meta["twilio_call_sid"] = participant.attributes.get("sip.twilio.callSid")
    call_meta["sip_call_id"] = participant.attributes.get("sip.callID")

    async def silence_nudge() -> None:
        await asyncio.sleep(SILENCE_NUDGE_SECONDS)
        if not transcript.turns:
            await session.generate_reply(
                instructions="Nobody has said anything yet. Say 'Hello?' like a caller checking the line."
            )

    async def call_timer() -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        await session.generate_reply(
            instructions="You're out of time. Politely say you need to go, thank them, and say goodbye."
        )
        await asyncio.sleep(8)
        await hangup()

    asyncio.create_task(silence_nudge())
    asyncio.create_task(call_timer())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm, agent_name=AGENT_NAME))
