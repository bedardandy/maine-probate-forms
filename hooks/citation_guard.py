#!/usr/bin/env python3
"""Claude Code hook: block a turn that cites the law badly, and attest every check.

Wire it as a **Stop** hook (runs when the agent finishes a turn) in
``.claude/settings.json``:

    {
      "hooks": {
        "Stop": [
          {"hooks": [{"type": "command",
                      "command": "python3 hooks/citation_guard.py"}]}
        ]
      }
    }

It reads the hook JSON on stdin, pulls the last assistant message out of the
transcript, runs the deterministic citation scanner (offline — no LLM, no
network), and on a finding emits ``{"decision":"block","reason":...}`` so Claude
Code feeds the problem back instead of finishing. Every check is attested to
``$ATTEST_LOG`` (if set), so you can prove the guard ran. Set
``$CITATION_GUARD_FORM`` to scope to a form's vocabulary, ``$CITATION_GUARD_LLM=1``
to also run the inspector LLM. Fails open: any internal error allows the turn
(the guard never bricks the agent). Use ``--text`` to test without a transcript.

    echo "see 18-C §9-999 and https://example.com/x" | python3 hooks/citation_guard.py --text -
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _last_assistant_text(transcript_path: str) -> str:
    """Best-effort: concatenate the text blocks of the last assistant message in a
    Claude Code transcript (JSONL). Returns "" if it can't be parsed."""
    try:
        lines = [ln for ln in pathlib.Path(transcript_path).read_text(
            encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return ""
    for ln in reversed(lines):
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        msg = ev.get("message", ev)
        role = msg.get("role") or ev.get("type")
        if role != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if any(parts):
                return "\n".join(p for p in parts if p)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="inspect this text (or '-' for stdin) instead of a transcript")
    a = ap.parse_args()

    form_id = os.environ.get("CITATION_GUARD_FORM") or None
    use_llm = os.environ.get("CITATION_GUARD_LLM") == "1"

    if a.text is not None:
        text = sys.stdin.read() if a.text == "-" else a.text
    else:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0                                   # fail open
        text = _last_assistant_text(payload.get("transcript_path", ""))

    if not (text or "").strip():
        return 0

    try:
        import guard
        res = guard.evaluate(text, form_id=form_id, llm=use_llm)
    except Exception as e:                              # never brick the agent
        print(f"citation_guard: internal error, allowing ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 0

    if res["block"]:
        # Stop-hook contract: block the stop and feed the reason back to the model.
        print(json.dumps({"decision": "block", "reason": res["reason"]}))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
