# Patient Simulator Voice Bot

An open-source voice bot that phones a medical office's AI receptionist and plays a realistic patient. It runs scripted scenarios, records both sides of every call, transcribes them, and drafts bug candidates, so you can stress-test a healthcare voice agent the way real callers would.

Use it to QA your own voice agent before launch, regression-test after prompt changes, or as a starting point for any outbound voice-testing bot.

**Stack:** LiveKit Agents in pipeline mode. Deepgram Nova-3 (speech to text), an OpenAI chat model (the patient), Cartesia Sonic-2 (text to speech), Silero VAD with LiveKit's turn detector, and Twilio Elastic SIP for the phone line. No realtime or speech-to-speech models, so you get text at every step.

> **Use responsibly.** Only call phone lines you own or have written permission to test. The bot dials `TARGET_NUMBER` and refuses unless that number is also listed in `ALLOWED_NUMBERS`.

## Use this template

Click **Use this template** on GitHub (or fork / clone), then follow the setup below.

## Use as a GitHub Action

Run the scenarios from CI, for example nightly or after every prompt change. Add your keys as repository secrets, then:

```yaml
name: Voice agent QA
on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 1"
jobs:
  call:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: ayanag5076-commits/patient-voice-bot@v1
        with:
          target-number: ${{ secrets.TARGET_NUMBER }}
          scenarios: "office_info,urgent_symptoms,privacy_probe"
          livekit-url: ${{ secrets.LIVEKIT_URL }}
          livekit-api-key: ${{ secrets.LIVEKIT_API_KEY }}
          livekit-api-secret: ${{ secrets.LIVEKIT_API_SECRET }}
          sip-outbound-trunk-id: ${{ secrets.SIP_OUTBOUND_TRUNK_ID }}
          deepgram-api-key: ${{ secrets.DEEPGRAM_API_KEY }}
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          cartesia-api-key: ${{ secrets.CARTESIA_API_KEY }}
          twilio-account-sid: ${{ secrets.TWILIO_ACCOUNT_SID }}
          twilio-auth-token: ${{ secrets.TWILIO_AUTH_TOKEN }}
```

Recordings, transcripts, and `bug_candidates.md` are uploaded as the `voice-bot-results` artifact. You still need the one-time Twilio and LiveKit trunk setup below.

## Setup (one time)

1. **Python 3.11+ and ffmpeg**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python agent.py download-files
   ```
2. **Keys:** you need accounts with LiveKit Cloud, Deepgram, OpenAI, Cartesia, and Twilio. Copy `.env.example` to `.env` and fill it in, including `TARGET_NUMBER`, `ALLOWED_NUMBERS`, and your test patient. `.env` is gitignored.
3. **Twilio:** buy one phone number and create an Elastic SIP trunk with a termination URI and a credential list. Turn on **Call Recording: Record from answer, dual channel**, then attach the number to the trunk.
4. **LiveKit outbound trunk:**
   ```bash
   cp outbound-trunk.example.json outbound-trunk.json
   lk sip outbound create outbound-trunk.json
   ```
   Put the `ST_...` ID it returns in `.env` as `SIP_OUTBOUND_TRUNK_ID`.

## Run

```bash
python run.py --all
```

This starts the worker and places each scenario call one after another. When the calls finish, it downloads the dual-channel recordings, transcribes each side, and drafts `reports/bug_candidates.md` for human review.

Run specific scenarios:
```bash
python run.py --scenario weekend_booking --scenario barge_in
```

## Output

Each call gets its own folder in `calls/` (gitignored, since recordings contain real voices and patient details).

| File | What it is |
|---|---|
| `recording.mp3` | Both sides of the call, from Twilio's dual-channel trunk recording |
| `transcript.txt` | Timestamped transcript. **A** = patient bot, **B** = clinic agent |
| `session_transcript.txt` | The bot's own view of the conversation, from LiveKit |
| `meta.json` | Scenario, timings, and the Twilio Call SID |
| `analysis.json` | The LLM reviewer's draft findings |

Confirm each candidate by listening, then write it up with [`docs/BUG_REPORT_TEMPLATE.md`](docs/BUG_REPORT_TEMPLATE.md).

## Scenarios

The 15 bundled scenarios in `scenarios.py` are written for an orthopedic practice:

- new-patient scheduling
- reschedule
- cancel then change your mind
- routine refill
- early controlled-substance refill
- office info
- insurance
- weekend booking
- urgent red-flag symptoms
- barge-in (interrupting)
- a vague, rambling caller
- privacy probe (asking about someone else's record)
- impossible dates
- prompt injection
- wrong specialty

### Adding your own

Add a dict to `SCENARIOS` in `scenarios.py`:

```python
{
    "id": "lost_referral",
    "situation": "Your primary care doctor says they faxed a referral last week.",
    "goal": "Confirm the referral arrived and book the first visit.",
    "behavior": "Be patient but persistent.",        # optional
    "persona": {"name": "...", "dob": "...", "phone": "..."},  # optional, else the test patient
    "min_endpointing_delay": 1.2,                    # optional
    "expect": "Checks for the referral honestly and offers a real slot.",
}
```

For a different specialty, set `CLINIC_DESCRIPTION` in `.env` and rewrite the scenarios to match.

## Files

| File | Purpose |
|---|---|
| `agent.py` | The LiveKit worker: voice pipeline, patient persona, outbound SIP dial, hang-up guard |
| `scenarios.py` | Personas, goals, and test behaviors |
| `run.py` | Places the calls and runs the post-processing |
| `fetch_recordings.py` | Downloads the dual-channel Twilio recordings |
| `transcribe_calls.py` | Splits the channels and transcribes each side with Whisper |
| `analyze.py` | An LLM reviewer that drafts bug candidates for human checking |
| `action.yml` | The GitHub Action wrapper for running calls in CI |
| `ARCHITECTURE.md` | How it works and why |

## Tuning tips

- `min_endpointing_delay` (default 1.2 s) controls how long the bot waits after the clinic stops talking. Raise it if the receptionist pauses mid-sentence.
- The `end_call` tool only hangs up after 45 seconds and a goodbye, so the bot doesn't drop the call during a recorded greeting.
- `MAX_CALL_SECONDS` caps every call (default 240).

## License

MIT. See [`LICENSE`](LICENSE).
