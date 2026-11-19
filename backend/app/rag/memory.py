"""Preference memory: learn lasting preferences from what users ask.

Pipeline for every question:
  1. Rule-based extractor (regex) – instant, works offline.
  2. LLM extractor (JSON) – catches phrasing the rules miss. Optional.
  3. Validate + normalise, then upsert with conflict resolution:
       * single-valued kinds (food, budget, pace, travel_with): newest wins,
         the older value is deactivated (e.g. "I eat meat now" replaces vegetarian);
       * multi-valued kinds (interest, avoid, access): de-duplicated;
       * contradictions: learning "avoid X" deactivates "interest X" and vice versa.

The profile used for retrieval and prompts = preferences form + active memories.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Memory, Preference
from app.rag.llm import LLMUnavailable, get_llm, parse_json

log = logging.getLogger(__name__)

KINDS = {"interest", "avoid", "food", "budget", "pace", "travel_with", "access"}
SINGLE = {"food", "budget", "pace", "travel_with"}
FILLER = re.compile(r"\b(a lot|very much|so much|really|too|also|and stuff|things?|places?)\b", re.I)

_RULES: list[tuple[str, re.Pattern, str | None]] = [
    ("food", re.compile(r"\bi(?:'m| am|m)\s+(?:a\s+|strictly\s+)?(vegetarian|vegan|jain|pescatarian|eggetarian)\b", re.I), None),
    ("food", re.compile(r"\bi (?:only )?eat (halal|kosher)\b", re.I), None),
    ("food", re.compile(r"\bi (?:eat|love) (?:non[- ]?veg|meat)\b", re.I), "non-vegetarian"),
    ("avoid", re.compile(r"\bi(?:'m| am|m) allergic to ([a-z ]{3,25})", re.I), None),
    ("avoid", re.compile(r"\bi (?:don't|do not|can't|cannot) eat ([a-z ]{3,25})", re.I), None),
    ("avoid", re.compile(r"\bi (?:hate|dislike|don't like|do not like|avoid|am scared of|fear) ([a-z ]{3,30})", re.I), None),
    ("interest", re.compile(r"\bi(?:'m| am)? (?:really )?(?:love|like|enjoy|prefer|into|am into|fond of) ([a-z ]{3,30})", re.I), None),
    ("interest", re.compile(r"\bi(?:'m| am) (?:a |an )?(photographer|foodie|history buff|trekker|hiker|birdwatcher)\b", re.I), None),
    ("budget", re.compile(r"\b(?:on a (?:tight |low |small )?budget|cheap(?:est)?|low[- ]cost|budget[- ]friendly)\b", re.I), "low"),
    ("budget", re.compile(r"\b(?:luxury|luxurious|five[- ]star|5[- ]star|premium)\b", re.I), "high"),
    ("pace", re.compile(r"\b(?:relaxed|slow[- ]paced|take it easy|not rushed)\b", re.I), "relaxed"),
    ("pace", re.compile(r"\b(?:packed|as much as possible|fast[- ]paced)\b", re.I), "packed"),
    ("travel_with", re.compile(r"\bwith (?:my )?(kids|children|family|parents|wife|husband|partner|girlfriend|boyfriend|friends)\b", re.I), None),
    ("access", re.compile(r"\b(wheelchair|can't walk (?:much|far)|cannot walk (?:much|far)|knee problem|elderly)\b", re.I), None),
]
_TRAVEL_WITH = {"kids": "family", "children": "family", "family": "family", "parents": "family",
                "wife": "couple", "husband": "couple", "partner": "couple", "girlfriend": "couple",
                "boyfriend": "couple", "friends": "friends"}


_VAGUE = {"this", "that", "it", "them", "you", "your", "the", "these", "those", "here", "there", "what", "how"}


def _clean(value: str) -> str:
    value = FILLER.sub("", value.lower())
    value = re.split(r"\b(?:in|at|near|during|when|while|but|because|so|for)\b|[,.!?]", value)[0]
    value = re.sub(r"^(?:to\s+)?(?:see|visit|try|do|eat|go to|explore|watch)\s+", "", value.strip())
    words = value.split()
    if not words or words[0] in _VAGUE:
        return ""
    return " ".join(words)[:60]


def extract_rules(text: str) -> list[tuple[str, str]]:
    found = []
    for kind, pattern, fixed in _RULES:
        for m in pattern.finditer(text):
            value = fixed or _clean(m.group(1))
            if kind == "travel_with":
                value = _TRAVEL_WITH.get(value, value)
            if len(value) >= 3:
                found.append((kind, value))
    return found


EXTRACT_PROMPT = """Extract LASTING travel preferences the traveller states about themselves in this message.
Only include things true beyond this one trip (diet, interests, dislikes, budget, pace, who they travel with, accessibility).
Ignore questions, one-off requests and anything about other people. If nothing, return an empty list.
Allowed kinds: interest, avoid, food, budget (low|medium|high), pace (relaxed|balanced|packed), travel_with (solo|couple|family|friends), access.
Return JSON: {{"memories": [{{"kind": "...", "value": "short lowercase phrase"}}]}}

Message: \"\"\"{text}\"\"\""""


def extract_llm(text: str) -> list[tuple[str, str]]:
    llm = get_llm()
    if not llm.available:
        return []
    try:
        data = parse_json(llm.complete([{"role": "user", "content": EXTRACT_PROMPT.format(text=text[:800])}],
                                       json_mode=True, max_tokens=400))
        return [(m["kind"], _clean(str(m["value"]))) for m in data.get("memories", [])
                if isinstance(m, dict) and m.get("kind") in KINDS and m.get("value")]
    except (LLMUnavailable, ValueError, KeyError, TypeError):
        return []


def learn_from_text(db: Session, user_id: int, text: str) -> list[Memory]:
    """Extract, de-duplicate and store memories. Returns the newly learned ones."""
    candidates = list(dict.fromkeys(extract_rules(text) + extract_llm(text)))
    if not candidates:
        return []
    active = db.scalars(select(Memory).where(Memory.user_id == user_id, Memory.active.is_(True))).all()
    learned = []
    for kind, value in candidates:
        if any(m.kind == kind and m.value == value for m in active):
            continue  # already known
        for m in active:
            if kind in SINGLE and m.kind == kind:
                m.active = False  # newest value wins
            if {kind, m.kind} == {"interest", "avoid"} and (value in m.value or m.value in value):
                m.active = False  # contradiction: latest statement wins
        mem = Memory(user_id=user_id, kind=kind, value=value, evidence=text.strip()[:300])
        db.add(mem)
        active.append(mem)
        learned.append(mem)
    db.commit()
    return learned


# ------------------------------------------------------------------- profile --

INTEREST_CUES = {
    "food": ["food", "dish", "cuisine", "restaurant", "market", "cafe", "eat"],
    "history": ["history", "historic", "ancient", "museum", "fort", "palace", "ruins", "heritage"],
    "culture": ["culture", "festival", "tradition", "temple", "local", "craft"],
    "nature": ["lake", "park", "forest", "valley", "waterfall", "garden", "river", "mountain"],
    "hiking": ["trek", "hike", "trail", "summit", "peak", "walk"],
    "adventure": ["rafting", "paragliding", "climb", "zipline", "adventure", "safari"],
    "spirituality": ["monastery", "temple", "shrine", "church", "mosque", "stupa", "gompa"],
    "shopping": ["market", "bazaar", "shop", "handicraft", "mall"],
    "nightlife": ["bar", "pub", "club", "nightlife", "live music"],
    "photography": ["viewpoint", "sunrise", "sunset", "panoramic", "view"],
    "wildlife": ["wildlife", "sanctuary", "national park", "zoo", "birds"],
    "beaches": ["beach", "coast", "island", "sea"],
}


@dataclass
class Profile:
    interests: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    food: str = "anything"
    budget: str = "medium"
    pace: str = "balanced"
    travel_with: str = "solo"
    access: list[str] = field(default_factory=list)
    notes: str | None = None

    def cues(self) -> list[tuple[str, list[str]]]:
        """(interest, words that indicate it) for re-ranking."""
        return [(i, INTEREST_CUES.get(i, [i])) for i in self.interests]

    def describe(self) -> str:
        lines = [f"- Interests: {', '.join(self.interests) or 'not specified'}",
                 f"- Budget: {self.budget}; pace: {self.pace}; travelling: {self.travel_with}",
                 f"- Food: {self.food}"]
        if self.avoid:
            lines.append(f"- Avoid: {', '.join(self.avoid)} (hard constraint)")
        if self.access:
            lines.append(f"- Accessibility: {', '.join(self.access)} (hard constraint)")
        if self.notes:
            lines.append(f"- Notes: {self.notes}")
        return "\n".join(lines)

    def as_query_hint(self) -> str:
        return " ".join(self.interests[:5] + ([self.food] if self.food != "anything" else []))


def load_profile(db: Session, user_id: int) -> Profile:
    pref = db.get(Preference, user_id)
    p = Profile()
    if pref:
        p.interests = list(pref.interests or [])
        p.budget, p.pace, p.travel_with, p.food, p.notes = pref.budget, pref.pace, pref.travel_with, pref.food, pref.notes
    for m in db.scalars(select(Memory).where(Memory.user_id == user_id, Memory.active.is_(True)).order_by(Memory.id)):
        if m.kind == "interest" and m.value not in p.interests:
            p.interests.append(m.value)
        elif m.kind == "avoid":
            p.avoid.append(m.value)
        elif m.kind == "access":
            p.access.append(m.value)
        elif m.kind in SINGLE:
            setattr(p, m.kind, m.value)  # learned values override the form (they're newer)
    return p
