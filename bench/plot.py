"""Scale curves for the README: accuracy, time and input tokens per question vs. number of records.

    uv run python -m bench.plot --run v0-reduced-haiku --out docs/bench/v0-scale-curves.svg

Static SVG (it is shown through an <img> on GitHub, so there is no hover layer; the README table next
to it is the table view). Three small multiples share the x axis (records, log scale); each has one
y scale. Series colours are the first three categorical slots of the dataviz reference palette
(validated all-pairs for three series), stepped for light and dark surfaces. Lines are direct-labelled.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "bench" / "results"
SERIES = [("B0", "B0 full context"), ("B1", "B1 Claude Code memory"), ("M", "M Mnemento")]
SCALES = [100, 1000, 10000]
PANELS = [
    ("accuracy", "Accuracy (all 12 questions)", lambda s: 100 * (s["dev"][0] + s["unseen"][0]) /
     max(1, s["dev"][1] + s["unseen"][1]), "linear", (0, 100), "%"),
    ("time", "Mean time per question", lambda s: s["time_mean"], "linear", None, "s"),
    ("tokens", "Input tokens per question", lambda s: s["in_per_q"], "log", None, ""),
]

STYLE = """
.viz { --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#8a8984; --grid:#e6e5e0;
       --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; }
@media (prefers-color-scheme: dark) {
  .viz { --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --muted:#8f8e86; --grid:#33332f;
         --s1:#3987e5; --s2:#d95926; --s3:#199e70; }
}
text { font-family: -apple-system, 'Segoe UI', Roboto, 'Noto Sans KR', sans-serif; fill: var(--ink2); }
.title { font-size: 13px; font-weight: 600; fill: var(--ink); }
.tick { font-size: 11px; fill: var(--muted); }
.label { font-size: 11px; fill: var(--ink2); }
.note { font-size: 10.5px; fill: var(--muted); }
.grid { stroke: var(--grid); stroke-width: 1; }
.axis { stroke: var(--muted); stroke-width: 1; }
"""


def _fmt(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.0f}%"
    if unit == "s":
        return f"{v:.0f}s"
    return f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}"


def render(summary: list[dict], meta: dict) -> str:
    data = {(s["scale"], s["system"]): s for s in summary if not s.get("not_measurable")}
    pw, ph, gap, left, top = 250, 190, 70, 56, 84
    width = left + 3 * pw + 2 * gap + 120
    height = top + ph + 78
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
           f'height="{height}" role="img" aria-labelledby="t d">',
           f'<title id="t">Mnemento v0 benchmark: accuracy, time and tokens by number of records</title>',
           '<desc id="d">Small multiples. B0 is measurable only at 100 records (context limit). '
           'The table in the README has every value.</desc>',
           f"<style>{STYLE}</style>", f'<g class="viz"><rect width="{width}" height="{height}" fill="var(--surface)"/>']
    # legend, one row above the panels
    lx = left
    for i, (_, name) in enumerate(SERIES, start=1):
        out.append(f'<line x1="{lx}" y1="22" x2="{lx + 18}" y2="22" stroke="var(--s{i})" stroke-width="2"/>'
                   f'<circle cx="{lx + 9}" cy="22" r="4" fill="var(--s{i})" stroke="var(--surface)" stroke-width="2"/>'
                   f'<text class="label" x="{lx + 24}" y="26">{name}</text>')
        lx += 24 + 6.2 * len(name) + 28
    full = max(s["n"] for s in summary if not s.get("not_measurable"))
    fewer = [f'{s["system"]} at {s["scale"]:,} = {s["n"] * 3 // full} repetition{"s" if s["n"] * 3 // full != 1 else ""}'
             for s in summary if not s.get("not_measurable") and s["n"] < full and s["system"] != "Mn"]
    out.append(f'<text class="note" x="{left}" y="46">{meta.get("plan_description", "")} · model {meta["model"]}'
               f' · B0 not measurable at 1,000+ records (context limit)</text>')
    if fewer:
        out.append(f'<text class="note" x="{left}" y="61">Note: {"; ".join(fewer)} (approved separately for cost)</text>')

    xs = {sc: i / (len(SCALES) - 1) for i, sc in enumerate(SCALES)}
    for p, (_, ptitle, fn, yscale, fixed, unit) in enumerate(PANELS):
        x0 = left + p * (pw + gap)
        vals = [fn(data[(sc, sy)]) for sc in SCALES for sy, _ in SERIES if (sc, sy) in data]
        if yscale == "log":
            lo = 10 ** math.floor(math.log10(min(vals)))
            hi = 10 ** math.ceil(math.log10(max(vals)))
            ticks = [10 ** k for k in range(int(math.log10(lo)), int(math.log10(hi)) + 1)]
            ymap = lambda v: top + ph - ph * (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))  # noqa: E731
        else:
            lo, hi = fixed if fixed else (0, max(vals) * 1.15)
            step = 25 if unit == "%" else _nice_step(hi)
            ticks = [lo + k * step for k in range(int((hi - lo) / step) + 1)]
            hi = max(hi, ticks[-1])
            ymap = lambda v: top + ph - ph * (v - lo) / (hi - lo)  # noqa: E731
        out.append(f'<text class="title" x="{x0}" y="{top - 14}">{ptitle}</text>')
        for t in ticks:
            y = ymap(t)
            out.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x0 + pw}" y2="{y:.1f}"/>'
                       f'<text class="tick" x="{x0 - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(t, unit)}</text>')
        out.append(f'<line class="axis" x1="{x0}" y1="{top + ph}" x2="{x0 + pw}" y2="{top + ph}"/>')
        for sc in SCALES:
            x = x0 + 14 + (pw - 28) * xs[sc]
            out.append(f'<text class="tick" x="{x:.1f}" y="{top + ph + 16}" text-anchor="middle">{sc:,}</text>')
        out.append(f'<text class="note" x="{x0 + pw / 2}" y="{top + ph + 32}" text-anchor="middle">records</text>')
        # dodge: series with (nearly) the same value at the same x are spread horizontally
        coords = {}
        for sc in SCALES:
            here = [(sy, ymap(fn(data[(sc, sy)]))) for sy, _ in SERIES if (sc, sy) in data]
            for sy, y in here:
                twins = [o for o, oy in here if abs(oy - y) < 6]
                dx = 0.0 if len(twins) < 2 else (twins.index(sy) - (len(twins) - 1) / 2) * 9
                coords[(sc, sy)] = (x0 + 14 + (pw - 28) * xs[sc] + dx, y)
        for i, (sy, name) in enumerate(SERIES, start=1):
            pts = [(*coords[(sc, sy)], fn(data[(sc, sy)])) for sc in SCALES if (sc, sy) in data]
            if len(pts) > 1:
                d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y, _) in enumerate(pts))
                out.append(f'<path d="{d}" fill="none" stroke="var(--s{i})" stroke-width="2" '
                           f'stroke-linejoin="round" stroke-linecap="round"/>')
            for x, y, v in pts:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="var(--s{i})" '
                           f'stroke="var(--surface)" stroke-width="2"><title>{name}: {_fmt(v, unit)}</title></circle>')
            if len(pts) == 1:  # a lone point (B0): label below it, clear of the lines
                x, y, v = pts[0]
                out.append(f'<text class="label" x="{x:.1f}" y="{y + 18:.1f}" text-anchor="middle">'
                           f'{sy} {_fmt(v, unit)}</text>')
            elif pts:  # direct label at the series' last point
                x, y, v = pts[-1]
                out.append(f'<text class="label" x="{x + 8:.1f}" y="{y + 4:.1f}">{sy} {_fmt(v, unit)}</text>')
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def _nice_step(hi: float) -> float:
    raw = hi / 4
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    d = RESULTS / args.run
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(summary, meta), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
