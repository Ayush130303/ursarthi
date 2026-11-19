"""Section-aware recursive chunking."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings

# Wikivoyage section -> retrieval category (used for preference matching)
SECTION_CATEGORIES = {
    "understand": "culture", "history": "culture", "talk": "culture", "respect": "culture",
    "culture": "culture", "see": "sightseeing", "do": "activities", "learn": "activities",
    "work": "practical", "buy": "shopping", "eat": "food", "drink": "nightlife",
    "sleep": "accommodation", "get in": "transport", "get around": "transport",
    "stay safe": "safety", "stay healthy": "safety", "connect": "practical", "cope": "practical",
    "go next": "nearby", "districts": "overview", "climate": "climate", "when to go": "climate",
    "highlights": "sightseeing", "food": "food", "budget": "budget", "tips": "practical",
    "overview": "overview",
}


def category_for(section: str) -> str:
    key = section.strip().lower()
    if key in SECTION_CATEGORIES:
        return SECTION_CATEGORIES[key]
    for name, cat in SECTION_CATEGORIES.items():
        if key.startswith(name):
            return cat
    return "general"


@dataclass
class Chunk:
    section: str
    category: str
    content: str


_HEADING = re.compile(r"^\s*(={2,4})\s*(.+?)\s*\1\s*$", re.M)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_wiki_sections(text: str) -> list[tuple[str, str]]:
    """Split '== Heading ==' formatted text into (top-level section, body)."""
    sections: list[tuple[str, str]] = []
    pos, current = 0, "Overview"
    for m in _HEADING.finditer(text):
        body = text[pos:m.start()].strip()
        if body:
            sections.append((current, body))
        if len(m.group(1)) == 2:  # only top-level headings switch the section
            current = m.group(2)
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        sections.append((current, tail))
    return sections


def split_markdown_sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"^#{1,3}\s+(.+)$", text, flags=re.M)
    if len(parts) == 1:
        return [("Overview", text.strip())] if text.strip() else []
    out = []
    if parts[0].strip():
        out.append(("Overview", parts[0].strip()))
    for i in range(1, len(parts), 2):
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            out.append((parts[i].strip(), body))
    return out


def recursive_split(text: str, size: int, overlap: int) -> list[str]:
    """Paragraph -> sentence packing with character overlap."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    units: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            units.append(para)
        else:
            sentences = _SENT.split(para)
            for s in sentences:
                while len(s) > size:  # pathological long sentence
                    units.append(s[:size])
                    s = s[size - overlap:]
                units.append(s)
    chunks: list[str] = []
    buf = ""
    for unit in units:
        if buf and len(buf) + len(unit) + 1 > size:
            chunks.append(buf.strip())
            tail = buf[-overlap:]
            cut = tail.find(" ")
            buf = (tail[cut + 1:] if cut != -1 else tail) + " " + unit
        else:
            buf = f"{buf}\n{unit}" if buf else unit
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def chunk_sections(sections: list[tuple[str, str]], min_chars: int = 60) -> list[Chunk]:
    s = get_settings()
    out: list[Chunk] = []
    for section, body in sections:
        for piece in recursive_split(body, s.chunk_size, s.chunk_overlap):
            if len(piece) >= min_chars:
                out.append(Chunk(section=section[:120], category=category_for(section), content=piece))
    return out
