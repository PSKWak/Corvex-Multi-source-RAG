"""Load raw sources from `data/` into `SourceDocument`s using `data/sources.yaml`."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import yaml

from ..config import Config
from ..schema import SourceDocument, SourceType
from .markdown import strip_frontmatter

_GROUP_TO_TYPE = {
    "documentation": SourceType.DOCUMENTATION,
    "forums": SourceType.FORUM,
    "blogs": SourceType.BLOG,
}


def _to_date(v) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def load_sources_registry(cfg: Config) -> list[dict]:
    """Flatten data/sources.yaml into normalised entries:
    {id, type, path (absolute), url, publish_date, product_version, author_role, group}."""
    reg_path = Path(cfg.section("paths", "sources_registry", default="data/sources.yaml"))
    if not reg_path.is_absolute():
        from ..config import REPO_ROOT
        reg_path = REPO_ROOT / reg_path
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    data_dir = cfg.data_dir

    out: list[dict] = []
    for group, gtype in _GROUP_TO_TYPE.items():
        block = reg.get(group) or {}
        base_url = block.get("base_url", "")
        default_version = block.get("default_version")
        for item in block.get("items", []):
            out.append(
                {
                    "id": item["id"],
                    "group": group,
                    "type": gtype,
                    "path": data_dir / item["path"],
                    "url": base_url + item.get("url", ""),
                    "publish_date": _to_date(item.get("publish_date")),
                    "product_version": item.get("version") or default_version,
                    "author_role": item.get("author_role"),
                }
            )
    return out


def _first_h1(md: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return m.group(1).strip() if m else fallback


def load_documentation(cfg: Config, entry: dict) -> SourceDocument:
    body = entry["path"].read_text(encoding="utf-8")
    return SourceDocument(
        source_id=entry["id"],
        source_type=SourceType.DOCUMENTATION,
        title=_first_h1(body, entry["id"]),
        url=entry["url"],
        body=body,
        publish_date=entry["publish_date"],
        stated_version=entry["product_version"],
        author_role=entry.get("author_role") or "staff",
    )


def load_blog(cfg: Config, entry: dict) -> SourceDocument:
    raw = entry["path"].read_text(encoding="utf-8")
    fm, rest, _ = strip_frontmatter(raw)
    return SourceDocument(
        source_id=entry["id"],
        source_type=SourceType.BLOG,
        title=fm.get("title") or _first_h1(rest, entry["id"]),
        url=fm.get("url") or entry["url"],
        body=rest,
        publish_date=_to_date(fm.get("publish_date")) or entry["publish_date"],
        stated_version=str(fm.get("product_version") or entry["product_version"] or "") or None,
        author_role=fm.get("author_role") or entry.get("author_role"),
        raw={"frontmatter": fm, "author": fm.get("author")},
    )


def _render_forum_body(title: str, posts: list[dict]) -> tuple[str, list[dict]]:
    """Build one text blob for the thread and annotate each post with its char span."""
    chunks_meta: list[dict] = []
    parts = [f"# {title}\n"]
    cursor = len(parts[0])
    for i, p in enumerate(posts):
        role = p.get("author_role") or "community"
        tag = "Question" if i == 0 else ("Accepted answer" if p.get("is_accepted") else "Reply")
        votes = p.get("votes")
        header = f"\n[{tag}] {p.get('author', 'unknown')} ({role})"
        if votes is not None:
            header += f", {votes} votes"
        header += f", {p.get('created_at', '')}:\n"
        text = (p.get("body") or "").strip() + "\n"
        block = header + text
        start = cursor
        parts.append(block)
        cursor += len(block)
        meta = dict(p)
        meta["_index"] = i
        meta["_tag"] = tag
        meta["_span"] = (start, cursor)
        chunks_meta.append(meta)
    return "".join(parts), chunks_meta


def load_forum_thread(cfg: Config, entry: dict) -> SourceDocument:
    data = json.loads(entry["path"].read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    body, annotated = _render_forum_body(data.get("title", entry["id"]), posts)
    return SourceDocument(
        source_id=entry["id"],
        source_type=SourceType.FORUM,
        title=data.get("title", entry["id"]),
        url=data.get("url") or entry["url"],
        body=body,
        publish_date=_to_date(data.get("created_at")) or entry["publish_date"],
        stated_version=str(data.get("product_version") or entry["product_version"] or "") or None,
        author_role=None,
        raw={"posts": annotated, "tags": data.get("tags", [])},
    )


_DISPATCH = {
    SourceType.DOCUMENTATION: load_documentation,
    SourceType.BLOG: load_blog,
    SourceType.FORUM: load_forum_thread,
}


def load_all(cfg: Config) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    for entry in load_sources_registry(cfg):
        docs.append(_DISPATCH[entry["type"]](cfg, entry))
    return docs
