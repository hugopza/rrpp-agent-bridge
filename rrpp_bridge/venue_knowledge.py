from __future__ import annotations

from pathlib import Path

from .workspace import SLUG_RE

MAX_KNOWLEDGE_CHARACTERS = 20_000


def load_venue_knowledge(venue_slug: str, directory: Path) -> str:
    """Read only the owner-managed Markdown file belonging to one venue."""
    if not SLUG_RE.fullmatch(venue_slug):
        return ""
    root = directory.resolve()
    path = (root / f"{venue_slug}.md").resolve()
    if path.parent != root or not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        return ""
    if "\x00" in content or len(content) > MAX_KNOWLEDGE_CHARACTERS:
        return ""
    return content
