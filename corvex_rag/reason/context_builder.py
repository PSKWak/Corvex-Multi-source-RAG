"""Build the numbered evidence context + citation map for the generator (DESIGN.md §8)."""
from __future__ import annotations

from ..config import Config
from ..schema import Citation, ResolvedContext
from ..textutil import count_tokens


def build_context(cfg: Config, resolved: ResolvedContext, *, token_budget: int) -> tuple[str, list[Citation]]:
    """Return (context_string, citations).

    Layout:
        ## Evidence
        [1] (documentation | v2.6 | 2025-05-01) Corvex docs > Configuration > workers
        <chunk text>

        [2] ...

        ## Known conflicts
        - <conflict note>

    Evidence keeps `resolved.evidence` order (resolution winners already lead; demoted
    entries are last but retained). Trimmed from the tail to fit `token_budget`, but a
    demoted entry that is the losing side of a surfaced conflict is kept so the answer can
    describe the superseded behaviour.
    """
    lines: list[str] = ["## Evidence"]
    citations: list[Citation] = []
    used = 0
    reserve = count_tokens("\n".join(resolved.conflict_notes)) + 64

    for i, sc in enumerate(resolved.evidence, start=1):
        m = sc.chunk.metadata
        date = m.published_at.isoformat() if m.published_at else "undated"
        crumb = m.title if not m.heading_path else f"{m.title} > " + " > ".join(m.heading_path)
        header = f"[{i}] ({m.source_type.value} | v{m.product_version or '?'} | {date}) {crumb}"
        block = f"{header}\n{sc.chunk.text}\n"
        btok = count_tokens(block)
        demoted = sc.components.get("demoted_by_resolution", False)
        if used + btok > token_budget - reserve and i > 3 and not demoted:
            break
        lines.append(block)
        used += btok
        citations.append(Citation(
            n=i, chunk_id=sc.chunk.chunk_id, source_type=m.source_type, source_id=m.source_id,
            title=m.title, url=m.url, heading_path=list(m.heading_path),
            product_version=m.product_version, published_at=m.published_at, char_span=tuple(m.char_span),
        ))

    if resolved.conflict_notes:
        lines.append("## Known conflicts between sources")
        lines.extend(f"- {n}" for n in resolved.conflict_notes)
        lines.append(
            "When a conflict above has a preferred side, lead with that; mention the other "
            "only as superseded/older behaviour."
        )

    return "\n".join(lines), citations
