"""Shell-command backend — drive ANY agent/model CLI.

Configured with an argv *list* template (never a shell string — no injection).
Placeholders {prompt} {model} {provider} {toolsets} are substituted as discrete
argv elements with shell=False. Covers `hermes chat`, `llm`, `ollama run`,
`codex`, etc.

  cmd_template: ["hermes","chat","--provider","{provider}","-m","{model}","-Q","-q","{prompt}"]
  cmd_template: ["ollama","run","{model}","{prompt}"]
  cmd_template: ["llm","-m","{model}","{prompt}"]
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from .base import BackendError, BackendResponse
from .pricing import cost_usd, estimate_tokens

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class ShellCmdBackend:
    cmd_template: list[str]
    model: str
    provider: str = "shell"
    toolsets: str = ""
    pricing: tuple[float, float] = (0.0, 0.0)
    noise_patterns: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)

    def __call__(self, prompt: str, *, timeout: float | None = None) -> BackendResponse:
        subs = {"prompt": prompt, "model": self.model, "provider": self.provider, "toolsets": self.toolsets}
        argv = []
        for tok in self.cmd_template:
            if tok == "{toolsets}" and not self.toolsets:
                continue  # drop empty toolset placeholder
            argv.append(tok.format(**subs) if "{" in tok else tok)
        argv += self.extra_args
        try:
            proc = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
        except Exception as exc:
            raise BackendError(f"shell_cmd {self.model}: {exc}") from exc
        if proc.returncode != 0:
            raise BackendError(f"shell_cmd rc={proc.returncode}: {(proc.stderr or proc.stdout)[:400]}")
        text = self._clean(proc.stdout)
        in_tok = estimate_tokens(prompt)
        out_tok = estimate_tokens(text)
        return BackendResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            usd=cost_usd(in_tok, out_tok, self.pricing), tokens_estimated=True,
            provider=self.provider, model=self.model,
        )

    def _clean(self, raw: str) -> str:
        noise = [re.compile(p, re.IGNORECASE) for p in self.noise_patterns]
        out = []
        for line in (raw or "").replace("\r", "\n").splitlines():
            line = _ANSI.sub("", line)
            if any(n.search(line.strip()) for n in noise):
                continue
            out.append(line)
        return "\n".join(out).strip()
