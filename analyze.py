"""Draft bug candidates from each transcript with an LLM reviewer.

This is a *triage* step, not the bug report. Listen to the flagged moments in
the recording, confirm or discard each one, then write BUGS.md yourself.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from scenarios import SCENARIOS

load_dotenv()
CALLS_DIR = Path(os.getenv("CALLS_DIR", "calls"))
client = OpenAI()

REVIEW_PROMPT = """You are a QA engineer reviewing a phone call between a simulated patient and a
medical office's AI receptionist. Find real problems in the RECEPTIONIST's behaviour:
wrong or invented facts, booking impossible or closed times, skipped identity verification,
privacy leaks, unsafe medical handling, lost context, contradictions, loops, ignoring the
caller, awkward turn-taking. Ignore punctuation and transcription noise.

Scenario goal: {goal}
A good receptionist should: {expect}

Transcript (timestamps are mm:ss from answer):
{transcript}

Return JSON: {{"goal_met": bool, "summary": str, "issues": [{{"title": str, "severity":
"High"|"Medium"|"Low", "timestamp": "mm:ss", "quote": str, "why": str, "expected": str}}]}}.
Only include issues you can point to in the transcript."""


def review(call_dir: Path) -> dict:
    meta = json.loads((call_dir / "meta.json").read_text())
    scenario = SCENARIOS.get(meta["scenario"], {})
    resp = client.chat.completions.create(
        model=os.getenv("REVIEW_MODEL", "gpt-6.1-sol"),
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": REVIEW_PROMPT.format(
            goal=meta["goal"], expect=scenario.get("expect", ""),
            transcript=(call_dir / "transcript.txt").read_text())}],
    )
    return json.loads(resp.choices[0].message.content)


def main() -> None:
    lines = ["# Bug candidates (auto-drafted, verify by listening)\n"]
    for call_dir in sorted(p.parent for p in CALLS_DIR.glob("*/transcript.txt")):
        result = review(call_dir)
        (call_dir / "analysis.json").write_text(json.dumps(result, indent=2))
        lines.append(f"## {call_dir.name}  (goal met: {result.get('goal_met')})\n\n{result.get('summary', '')}\n")
        for i in result.get("issues", []):
            lines.append(f"- **[{i['severity']}] {i['title']}** at {i['timestamp']}: \"{i['quote']}\" "
                         f"Why: {i['why']} Expected: {i['expected']}")
        lines.append("")
    Path("reports").mkdir(exist_ok=True)
    Path("reports/bug_candidates.md").write_text("\n".join(lines))
    print("wrote reports/bug_candidates.md")


if __name__ == "__main__":
    main()
