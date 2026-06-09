"""Agent-harness backend — a full CLI coding agent as one leaf (v3).

Where every other backend is one model call, an agent leaf is a bounded
*agentic task*: the harness (Codex CLI, Claude Code, or any generic agent CLI)
gets a prompt and a workspace, uses its own tools — read/edit files, run
commands — and returns a final answer plus REAL token usage. The engine treats
it like any leaf: budget-gated, journaled, resumable, schema-enforced.

Adapters:
- ``codex``  — ``codex exec --json`` (ChatGPT-subscription gpt-5.5, $0).
  Sandbox levels map to ``-s read-only|workspace-write`` or the explicit
  ``danger-full-access`` bypass. Native ``--output-schema`` when the leaf has
  a JSON schema. Continuation via ``codex exec resume <thread_id>``.
- ``claude`` — ``claude -p --output-format json``. Tool surface controlled by
  ``allowed_tools`` (or the explicit ``skip_permissions`` bypass). Real usage
  and ``total_cost_usd`` from the result envelope. Continuation via
  ``claude -p --resume <session_id>`` — schema repair turns reuse the session
  instead of re-running the whole task.
- ``generic`` — argv ``cmd_template`` with ``{prompt}``/``{workspace}``
  placeholders (e.g. ``hermes chat``); stdout is the answer, usage estimated.

The full harness transcript is persisted to ``transcript_path`` so every
agentic decision is auditable after the run.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .base import BackendError, BackendResponse
from .pricing import estimate_tokens

DEFAULT_TIMEOUT_S = 1800.0


@dataclass
class AgentCLIBackend:
    harness: str                      # codex | claude | generic
    model: str = ""
    provider: str = "agent"
    workspace: str = ""               # cwd for the harness process
    transcript_path: str = ""         # JSONL/JSON transcript persisted here
    continue_id: str = ""             # resume a prior session/thread
    sandbox: str = "workspace-write"  # codex: read-only | workspace-write | danger-full-access
    allowed_tools: list = field(default_factory=list)   # claude --allowedTools
    add_dirs: list = field(default_factory=list)        # claude --add-dir
    skip_permissions: bool = False    # claude explicit bypass (degated boxes)
    system_prompt: str = ""           # appended system prompt (agent "type")
    cmd_template: list = field(default_factory=list)    # generic harness argv
    extra_args: list = field(default_factory=list)
    schema_file: str = ""             # codex --output-schema (written by caller)
    default_timeout: float = DEFAULT_TIMEOUT_S
    bin: str = ""                     # override the harness executable path
    session_id: str = ""              # set after the first call; reused by repair turns

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        run = {"codex": self._run_codex, "claude": self._run_claude,
               "generic": self._run_generic}.get(self.harness)
        if run is None:
            raise BackendError(f"unknown agent harness {self.harness!r} "
                               f"(expected codex|claude|generic)")
        return run(prompt, timeout or self.default_timeout)

    # -- codex ------------------------------------------------------------
    def _run_codex(self, prompt: str, timeout: float) -> BackendResponse:
        resume_id = self.session_id or self.continue_id
        argv = [self.bin or "codex", "exec"]
        if resume_id:
            argv += ["resume", resume_id]
        argv += ["--json", "--skip-git-repo-check"]
        if self.model:
            argv += ["-m", self.model]
        if self.sandbox == "danger-full-access":
            argv += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            argv += ["-s", self.sandbox]
        if self.workspace:
            argv += ["-C", self.workspace]
        if self.schema_file and not resume_id:
            argv += ["--output-schema", self.schema_file]
        argv += self.extra_args
        if self.system_prompt:
            prompt = f"{self.system_prompt}\n\n{prompt}"
        argv.append(prompt)

        stdout = self._exec(argv, timeout)
        self._persist(stdout)
        text, usage, thread_id = "", {}, ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            kind = ev.get("type")
            if kind == "thread.started":
                thread_id = ev.get("thread_id") or ""
            elif kind == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                text = ev["item"].get("text") or text
            elif kind == "turn.completed":
                usage = ev.get("usage") or usage
            elif kind in ("turn.failed", "error"):
                raise BackendError(f"codex agent: {json.dumps(ev)[:400]}")
        if not text:
            raise BackendError(f"codex agent produced no agent_message: {stdout[:400]}")
        self.session_id = thread_id or self.session_id
        return BackendResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0))
                          + int(usage.get("reasoning_output_tokens", 0)),
            usd=0.0, tokens_estimated=not usage,
            provider=self.provider, model=self.model or "codex",
            session_id=self.session_id,
        )

    # -- claude -----------------------------------------------------------
    def _run_claude(self, prompt: str, timeout: float) -> BackendResponse:
        resume_id = self.session_id or self.continue_id
        argv = [self.bin or "claude", "-p", prompt, "--output-format", "json"]
        if self.model:
            argv += ["--model", self.model]
        if resume_id:
            argv += ["--resume", resume_id]
        if self.allowed_tools:
            argv += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.skip_permissions:
            argv += ["--dangerously-skip-permissions"]
        for d in self.add_dirs:
            argv += ["--add-dir", d]
        if self.system_prompt:
            argv += ["--append-system-prompt", self.system_prompt]
        argv += self.extra_args

        stdout = self._exec(argv, timeout)
        self._persist(stdout)
        try:
            env = json.loads(stdout.strip().splitlines()[-1])
        except Exception as exc:
            raise BackendError(f"claude agent: unparseable envelope: {exc}: "
                               f"{stdout[:400]}") from exc
        if env.get("is_error"):
            raise BackendError(f"claude agent error: {str(env.get('result'))[:400]}")
        usage = env.get("usage") or {}
        self.session_id = env.get("session_id") or self.session_id
        return BackendResponse(
            text=str(env.get("result") or ""),
            input_tokens=int(usage.get("input_tokens", 0))
                         + int(usage.get("cache_creation_input_tokens", 0))
                         + int(usage.get("cache_read_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            usd=float(env.get("total_cost_usd") or 0.0),
            tokens_estimated=not usage,
            provider=self.provider, model=self.model or "claude",
            session_id=self.session_id,
        )

    # -- generic ----------------------------------------------------------
    def _run_generic(self, prompt: str, timeout: float) -> BackendResponse:
        if not self.cmd_template:
            raise BackendError("generic agent harness needs a cmd_template")
        if self.system_prompt:
            prompt = f"{self.system_prompt}\n\n{prompt}"
        subs = {"prompt": prompt, "workspace": self.workspace, "model": self.model}
        argv = [tok.format(**subs) if "{" in tok else tok for tok in self.cmd_template]
        argv += self.extra_args
        stdout = self._exec(argv, timeout)
        self._persist(stdout)
        text = stdout.strip()
        in_tok, out_tok = estimate_tokens(prompt), estimate_tokens(text)
        return BackendResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            usd=0.0, tokens_estimated=True,
            provider=self.provider, model=self.model or "generic",
        )

    # -- shared -----------------------------------------------------------
    def _exec(self, argv: list, timeout: float) -> str:
        try:
            # stdin must be detached: harnesses (codex exec) read a non-TTY
            # stdin as "additional input" and hang or misbehave under a runner.
            proc = subprocess.run(
                argv, text=True, capture_output=True, timeout=timeout,
                check=False, cwd=self.workspace or None,
                stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"agent {self.harness} timed out after {timeout}s",
                               retryable=False) from exc
        except Exception as exc:
            raise BackendError(f"agent {self.harness}: {exc}") from exc
        if proc.returncode != 0:
            # Persist whatever the harness said before failing the leaf — the
            # diagnostic is usually in the stdout event stream, not stderr.
            self._persist((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or ""))
            detail = (proc.stderr or "").strip().splitlines()
            detail = detail[-1] if detail else ""
            raise BackendError(
                f"agent {self.harness} rc={proc.returncode}: {detail[:300]} | "
                f"stdout tail: {(proc.stdout or '')[-500:]}")
        return proc.stdout or ""

    def _persist(self, raw: str) -> None:
        if not self.transcript_path:
            return
        try:
            p = Path(self.transcript_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(raw if raw.endswith("\n") else raw + "\n")
        except OSError:
            pass  # transcript is observability, never a leaf failure
