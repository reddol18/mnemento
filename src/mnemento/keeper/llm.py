"""LLM adapters. The Keeper only ever asks for *structured* output (a JSON object matching a
JSON Schema), so the interface is a single method. Swap adapters to change model/provider.

- `ClaudeCLIAdapter` — runs `claude -p` (Claude Code CLI) with `--json-schema`. Uses the local
  Claude Code login, so no API key is needed. Reports tokens, cost and model-vs-process time.
- `ScriptedLLM` — returns canned responses; used by tests (no network, no cost).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..errors import MnementoError
from .trace import LLMUsage


class LLMError(MnementoError):
    """The model call failed or returned something unusable."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: LLMUsage


class LLMAdapter(Protocol):
    model: str

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any], stage: str) -> LLMResult: ...


class ClaudeCLIAdapter:
    """Structured-output calls through the Claude Code CLI.

    Every call is isolated: no tools, no MCP servers, no settings files, no session persistence,
    a replaced system prompt, and a throwaway working directory (so no project CLAUDE.md).
    """

    def __init__(self, model: str = "haiku", executable: str | None = None, timeout_s: float = 180,
                 effort: str | None = None, max_thinking_tokens: int | None = None,
                 tools: list[str] | None = None, workdir: str | None = None):
        self.model = model
        self.tools = tools  # e.g. ["Read", "Grep", "Glob"]; None = no tools at all
        self.workdir = workdir  # run in this directory (tools see it); None = throwaway directory
        self.effort = effort  # CLI --effort (low|medium|high|xhigh|max); None = CLI default
        self.max_thinking_tokens = max_thinking_tokens  # MAX_THINKING_TOKENS env; 0 disables thinking
        self.executable = executable or shutil.which("claude") or "claude"
        self.timeout_s = timeout_s

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any], stage: str) -> LLMResult:
        with tempfile.TemporaryDirectory(prefix="mnemento-llm-") as tmp:
            sys_file = Path(tmp) / "system.txt"
            sys_file.write_text(system, encoding="utf-8")
            cmd = [
                self.executable, "-p",
                "--output-format", "json",
                "--no-session-persistence",
                "--model", self.model,
                *(["--tools", ",".join(self.tools), "--allowedTools", ",".join(self.tools)]
                  if self.tools else ["--tools", ""]),
                "--strict-mcp-config",
                "--setting-sources", "",
                "--system-prompt-file", str(sys_file),
                "--json-schema", json.dumps(schema, ensure_ascii=False),
            ]
            if self.effort:
                cmd += ["--effort", self.effort]
            env = {**os.environ, "NO_COLOR": "1"}
            if self.max_thinking_tokens is not None:
                env["MAX_THINKING_TOKENS"] = str(self.max_thinking_tokens)
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                    cwd=self.workdir or tmp, timeout=self.timeout_s, env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"claude CLI timed out after {self.timeout_s}s") from exc
            wall_ms = (time.perf_counter() - t0) * 1000
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude CLI returned non-JSON (exit {proc.returncode}): "
                           f"{(proc.stdout or proc.stderr)[:300]}") from exc
        if out.get("is_error") or out.get("subtype") != "success":
            raise LLMError(f"claude CLI error: {str(out.get('result'))[:300]}")
        data = out.get("structured_output")
        if not isinstance(data, dict):
            raise LLMError("claude CLI returned no structured_output")
        u = dict(out.get("usage") or {})
        per_model = out.get("modelUsage") or {}
        if per_model:  # totals over every turn and every model the CLI used
            u["input_tokens"] = sum(m.get("inputTokens", 0) for m in per_model.values())
            u["output_tokens"] = sum(m.get("outputTokens", 0) for m in per_model.values())
            u["cache_read_input_tokens"] = sum(m.get("cacheReadInputTokens", 0) for m in per_model.values())
            u["cache_creation_input_tokens"] = sum(m.get("cacheCreationInputTokens", 0) for m in per_model.values())
        model_ms = out.get("duration_api_ms")
        usage = LLMUsage(
            stage=stage,
            model=next(iter(out.get("modelUsage") or {}), self.model),
            input_tokens=int(u.get("input_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            thinking_tokens=int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
            cost_usd=out.get("total_cost_usd"),
            wall_ms=round(wall_ms, 1),
            model_ms=float(model_ms) if model_ms is not None else None,
            overhead_ms=round(wall_ms - float(model_ms), 1) if model_ms is not None else None,
            turns=int(out.get("num_turns") or 1),
        )
        return LLMResult(data, usage)


@dataclass
class ScriptedLLM:
    """Test double. Each call pops the next response (a dict, or a callable(system, prompt) -> dict).
    Records every call so tests can assert what the model was shown."""

    responses: list[dict[str, Any] | Callable[[str, str], dict[str, Any]]] = field(default_factory=list)
    model: str = "scripted"
    calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_per_char: float = 0.25

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any], stage: str) -> LLMResult:
        self.calls.append({"stage": stage, "system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise LLMError("ScriptedLLM has no more responses")
        nxt = self.responses.pop(0)
        data = nxt(system, prompt) if callable(nxt) else nxt
        usage = LLMUsage(
            stage=stage, model=self.model,
            input_tokens=int((len(system) + len(prompt)) * self.tokens_per_char),
            output_tokens=int(len(json.dumps(data, ensure_ascii=False)) * self.tokens_per_char),
            cost_usd=0.0, wall_ms=0.0, model_ms=0.0, overhead_ms=0.0,
        )
        return LLMResult(data, usage)
