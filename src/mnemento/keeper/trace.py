"""Per-request measurement: stage timings and LLM token usage (benchmark material, ADR-0008)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator


@dataclass
class LLMUsage:
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0  # part of output_tokens
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None
    wall_ms: float = 0.0  # whole call as seen by us (incl. process start for CLI adapters)
    model_ms: float | None = None  # time spent waiting on the model API, if the adapter knows it
    overhead_ms: float | None = None  # wall_ms - model_ms: process start, CLI bookkeeping
    attempt: int = 1
    turns: int = 1  # model turns inside the call (tool-using agents take several)


@dataclass
class Trace:
    stages: dict[str, float] = field(default_factory=dict)  # stage -> ms (wall, summed)
    llm: list[LLMUsage] = field(default_factory=list)
    path: str = ""  # "fast" (no LLM) | "llm" | "structured"
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    total_ms: float = 0.0

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - t) * 1000

    def add_llm(self, usage: LLMUsage) -> None:
        self.llm.append(usage)

    def finish(self) -> "Trace":
        self.total_ms = (time.perf_counter() - self._t0) * 1000
        return self

    @property
    def llm_calls(self) -> int:
        return len(self.llm)

    def totals(self) -> dict[str, Any]:
        model_ms = sum(u.model_ms or 0.0 for u in self.llm)
        overhead_ms = sum(u.overhead_ms or 0.0 for u in self.llm)
        llm_wall = sum(u.wall_ms for u in self.llm)
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": sum(u.input_tokens for u in self.llm),
            "output_tokens": sum(u.output_tokens for u in self.llm),
            "thinking_tokens": sum(u.thinking_tokens for u in self.llm),
            "cache_read_input_tokens": sum(u.cache_read_input_tokens for u in self.llm),
            "cache_creation_input_tokens": sum(u.cache_creation_input_tokens for u in self.llm),
            "cost_usd": round(sum(u.cost_usd or 0.0 for u in self.llm), 6),
            "total_ms": round(self.total_ms, 1),
            # ① process start / CLI overhead, ② model response, ③ DB + code (everything else)
            "llm_overhead_ms": round(overhead_ms, 1),
            "llm_model_ms": round(model_ms, 1),
            "code_ms": round(max(self.total_ms - llm_wall, 0.0), 1),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "stages_ms": {k: round(v, 2) for k, v in self.stages.items()},
            "llm": [asdict(u) for u in self.llm],
            "totals": self.totals(),
        }
