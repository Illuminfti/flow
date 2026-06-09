"""Fake agent-harness executables for hermetic v3 tests.

``write_fake(tmp_path, kind)`` writes an executable that speaks the real
harness's stdout protocol (codex JSONL events / claude JSON envelope), proves
workspace execution by dropping a file in its cwd, logs every invocation's
argv to ``calls.log``, and — when ``FAKE_MALFORMED_FIRST`` is set — returns a
non-JSON answer on the first call and a valid one on resume, exercising the
continuation repair path.
"""
import stat
from pathlib import Path

_CODEX = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
with (state / "calls.log").open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
Path("agent-was-here.txt").write_text(os.environ.get("FAKE_MARK", "mark"))

resumed = "resume" in sys.argv
answer = os.environ.get("FAKE_ANSWER", '{"ok": true}')
if os.environ.get("FAKE_MALFORMED_FIRST") and not resumed:
    answer = "definitely not json"
print(json.dumps({"type": "thread.started", "thread_id": "fake-thread-1"}))
print(json.dumps({"type": "item.completed",
                  "item": {"id": "item_0", "type": "agent_message", "text": answer}}))
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": 1000, "cached_input_tokens": 100,
                            "output_tokens": 50, "reasoning_output_tokens": 10}}))
'''

_CLAUDE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
with (state / "calls.log").open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
Path("agent-was-here.txt").write_text(os.environ.get("FAKE_MARK", "mark"))

resumed = "--resume" in sys.argv
answer = os.environ.get("FAKE_ANSWER", '{"ok": true}')
if os.environ.get("FAKE_MALFORMED_FIRST") and not resumed:
    answer = "definitely not json"
print(json.dumps({"type": "result", "is_error": False, "result": answer,
                  "session_id": "fake-session-1", "total_cost_usd": 0.012,
                  "usage": {"input_tokens": 700, "cache_creation_input_tokens": 200,
                            "cache_read_input_tokens": 100, "output_tokens": 40}}))
'''


def write_fake(dest_dir: Path, kind: str) -> str:
    src = {"codex": _CODEX, "claude": _CLAUDE}[kind]
    path = Path(dest_dir) / f"fake-{kind}"
    path.write_text(src, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return str(path)
