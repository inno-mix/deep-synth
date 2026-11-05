"""Render a validated Report model to Markdown (deterministic, so output is always well-formed)."""

import re

from app.schemas import Report

_CITE = re.compile(r"\[(\d+)\]")


def _link_citations(text: str) -> str:
    # [3] -> [[3]](#ref-3) so citations are clickable in any Markdown viewer
    return _CITE.sub(lambda m: f"[[{m.group(1)}]](#ref-{m.group(1)})", text)


def _cell(text: str) -> str:
    return _link_citations(text.replace("|", "\\|").replace("\n", " "))


def render_markdown(r: Report) -> str:
    out = [f"# {r.title}", ""]
    out.append(
        f"> **Topic:** {r.topic}  \n> **Generated:** {r.generated_at:%Y-%m-%d %H:%M} UTC · "
        f"**Model:** `{r.model}` · **Verified findings:** {r.stats.get('verified', '?')} · "
        f"**Sources:** {len(r.citations)}"
    )
    out += ["", "## TL;DR", "", _link_citations(r.tldr), ""]

    if r.comparison_options and r.comparison:
        n = len(r.comparison_options)
        out += ["## At a glance", ""]
        out.append("| Dimension | " + " | ".join(_cell(o) for o in r.comparison_options) + " |")
        out.append("|---" * (n + 1) + "|")
        for row in r.comparison:
            vals = (row.values + [""] * n)[:n]
            out.append(f"| {_cell(row.dimension)} | " + " | ".join(_cell(v) for v in vals) + " |")
        out.append("")

    for sec in r.sections:
        out += [f"## {sec.heading}", "", _link_citations(sec.body.strip()), ""]

    if r.caveats:
        out += ["## Caveats", ""] + [f"- {c}" for c in r.caveats] + [""]

    out += ["## Sources", ""]
    out += [f'{c.n}. <a id="ref-{c.n}"></a>[{c.title}]({c.url})' for c in r.citations]
    return "\n".join(out).strip() + "\n"
