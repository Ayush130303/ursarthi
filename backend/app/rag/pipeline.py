"""The three backend use-cases: plan a route, suggest, answer questions."""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Source, Trip, TripMessage, User
from app.rag import prompts
from app.rag.embeddings import tokenize
from app.rag.knowledge import add_history, ensure_knowledge
from app.rag.llm import LLMUnavailable, get_llm, parse_json
from app.rag.memory import Profile, learn_from_text, load_profile
from app.rag.retriever import Passage, format_context, preference_boost, retrieve
from app.services import routing, tsp

log = logging.getLogger(__name__)
_SENT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.replace("\n", " ")) if 25 < len(s.strip()) < 400]


def route_names(trip: Trip) -> list[str]:
    return [trip.stops[i]["name"].split(",")[0] for i in trip.order]


# ------------------------------------------------------------------ route --

def plan_trip(db: Session, user: User, stops: list[dict], return_to_start: bool = False) -> Trip:
    """stops[0] is the starting point. Computes the best visiting order."""
    matrix, mode = routing.distance_matrix(stops)
    order, total_m = tsp.solve(matrix, return_to_start)
    if return_to_start:
        order = order + [0]
    legs = [round((matrix[a][b] or 0) / 1000, 2) for a, b in zip(order, order[1:])]
    geometry = routing.route_geometry([stops[i] for i in order])
    names = [stops[i]["name"].split(",")[0] for i in order]
    title = " → ".join(names[:4]) + (" …" if len(names) > 4 else "")
    trip = Trip(user_id=user.id, title=title[:200], stops=stops, order=order, legs_km=legs,
                total_km=round(total_m / 1000, 2), distance_mode=mode, geometry=geometry)
    db.add(trip)
    db.commit()
    return trip


# ------------------------------------------------------------- suggestions --

def _candidates(db: Session, trip: Trip, profile: Profile, limit: int = 8) -> list[dict]:
    """Nearby Wikipedia places (with coordinates) that are not already stops."""
    rows = db.execute(
        select(Source, Chunk.content).join(Chunk, Chunk.source_id == Source.id)
        .where(Source.id.in_(trip.source_ids or [-1]), Source.kind == "wikipedia",
               Source.lat.is_not(None), Chunk.category == "sightseeing")
    ).all()
    seen, out = set(), []
    for src, text in rows:
        if src.id in seen:
            continue
        seen.add(src.id)
        nearest = min(routing.haversine_m(src.lat, src.lon, s["lat"], s["lon"]) for s in trip.stops)
        if nearest < 300:  # it's basically one of the stops
            continue
        boost, reasons = preference_boost(text, "sightseeing", profile)
        out.append({"id": src.id, "name": src.title, "lat": src.lat, "lon": src.lon, "url": src.url,
                    "km_from_route": round(nearest / 1000, 1), "about": (_sentences(text) or [text[:200]])[0],
                    "score": boost - nearest / 50_000, "reasons": reasons})
    out.sort(key=lambda c: -c["score"])
    return out[:limit]


def suggest(db: Session, user: User, trip: Trip) -> dict:
    trip.source_ids = ensure_knowledge(db, [trip.stops[i] for i in dict.fromkeys(trip.order)])
    profile = load_profile(db, user.id)
    names = route_names(trip)
    query = f"best things to see, local food and travel tips in {', '.join(dict.fromkeys(names))} {profile.as_query_hint()}"
    passages = retrieve(db, query, profile, trip.source_ids, user.id, k=8)
    cands = _candidates(db, trip, profile)
    sources = [p.to_source(n) for n, p in enumerate(passages, 1)]

    result = None
    llm = get_llm()
    if llm.available and passages:
        cand_text = "\n".join(f"id={c['id']}: {c['name']} ({c['km_from_route']} km away) – {c['about']}" for c in cands) or "(none)"
        try:
            data = parse_json(llm.complete([
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.SUGGEST.format(
                    profile=profile.describe(), route=" → ".join(names), km=trip.total_km,
                    mode="road distance" if trip.distance_mode == "road" else "estimated distance",
                    context=format_context(passages), candidates=cand_text)},
            ], json_mode=True))
            by_id = {c["id"]: c for c in cands}
            recs = [{**by_id[r["id"]], "reason": str(r.get("reason", ""))[:300]}
                    for r in data.get("recommendations", []) if isinstance(r, dict) and r.get("id") in by_id]  # drop hallucinated places
            result = {"summary": str(data.get("summary", "")), "tips": [str(t) for t in data.get("tips", [])][:6],
                      "recommendations": recs[:3], "generated_by": "llm"}
        except (LLMUnavailable, ValueError, TypeError, AttributeError) as exc:
            log.info("Suggestion LLM fallback: %s", exc)
    if result is None:
        result = _fallback_suggestions(trip, names, passages, cands)
    result["sources"] = sources
    trip.suggestions = result
    db.commit()
    return result


def _fallback_suggestions(trip: Trip, names: list[str], passages: list[Passage], cands: list[dict]) -> dict:
    tips, used = [], set()
    for n, p in enumerate(passages, 1):
        for sent in _sentences(p.content)[:2]:
            if sent not in used and len(tips) < 4:
                used.add(sent)
                tips.append(f"{sent} [{n}]")
    if trip.total_km > 150:
        tips.append(f"The full route is about {trip.total_km} km, so plan rest breaks or split it over two days.")
    recs = [{**c, "reason": "; ".join(c["reasons"]) or c["about"]} for c in cands[:3]]
    summary = (f"Visit {', '.join(names[1:]) or names[0]} starting from {names[0]}, about {trip.total_km} km in total. "
               "Tips below come straight from the travel guides (AI writing is offline).")
    return {"summary": summary, "tips": tips, "recommendations": recs, "generated_by": "extractive"}


# --------------------------------------------------------------------- ask --

def ask(db: Session, user: User, trip: Trip, question: str) -> dict:
    learned = learn_from_text(db, user.id, question)          # 1. remember lasting preferences
    profile = load_profile(db, user.id)                         # 2. form + memories
    if not trip.source_ids:
        trip.source_ids = ensure_knowledge(db, [trip.stops[i] for i in dict.fromkeys(trip.order)])
    history = list(trip.messages)[-4:]
    query = question
    if len(tokenize(question)) < 5 and history:                 # short follow-up: add context
        query = f"{history[-2].content if len(history) >= 2 else ''} {question}"
    passages = retrieve(db, f"{query} {' '.join(route_names(trip))}", profile, trip.source_ids, user.id)
    sources = [p.to_source(n) for n, p in enumerate(passages, 1)]

    answer, used_llm = None, False
    llm = get_llm()
    if llm.available:
        msgs = [{"role": "system", "content": prompts.SYSTEM}]
        msgs += [{"role": m.role, "content": m.content[:1200]} for m in history]
        msgs.append({"role": "user", "content": prompts.ASK.format(
            profile=profile.describe(), route=" → ".join(route_names(trip)), km=trip.total_km,
            context=format_context(passages) or "(no passages found)", question=question)})
        try:
            answer, used_llm = llm.complete(msgs).strip(), True
        except LLMUnavailable:
            pass
    if not answer:
        answer = _extractive_answer(question, passages)

    db.add(TripMessage(trip_id=trip.id, role="user", content=question))
    reply = TripMessage(trip_id=trip.id, role="assistant", content=answer, sources=sources)
    db.add(reply)
    add_history(db, user.id, trip.id, trip.title, question, answer)  # 3. recall it in future searches
    db.commit()
    return {"message_id": reply.id, "answer": answer, "sources": sources, "used_llm": used_llm,
            "learned": [{"id": m.id, "kind": m.kind, "value": m.value} for m in learned]}


def _extractive_answer(question: str, passages: list[Passage]) -> str:
    if not passages:
        return ("I couldn't find anything about that in the guides for this route yet. "
                "Try asking about a specific stop, food, or things to do.")
    q = set(tokenize(question))
    ranked = sorted(((len(q & set(tokenize(s))) - j * 0.1 - n * 0.05, n, s)
                     for n, p in enumerate(passages, 1) for j, s in enumerate(_sentences(p.content)[:8])), reverse=True)
    lines, per = [], {}
    for _, n, s in ranked:
        if per.get(n, 0) < 2 and len(lines) < 5:
            per[n] = per.get(n, 0) + 1
            lines.append(f"- {s} [{n}]")
    return ("Here's what the guides say:\n" + "\n".join(lines) +
            "\n\n_(AI writing is offline, so this answer is assembled from the sources.)_")
