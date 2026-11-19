from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import ai_rate_limit, get_current_user
from app.db.session import get_db
from app.models import Source, Trip, User
from app.rag.memory import load_profile
from app.rag.pipeline import ask, plan_trip, route_names, suggest
from app.rag.retriever import retrieve
from app.rag.vector_store import get_vector_store
from app.schemas import AskRequest, TripCreate, TripOut, TripSummary
from app.services.geo import GeocodingUnavailable, search_places

router = APIRouter(tags=["Trips"])


@router.get("/places/search")
def places_search(q: str = Query(min_length=2, max_length=200)):
    """Worldwide place search (Nominatim, with Photon as backup)."""
    try:
        return search_places(q)
    except GeocodingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _own_trip(db: Session, user: User, trip_id: int) -> Trip:
    trip = db.get(Trip, trip_id)
    if not trip or trip.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@router.post("/trips", response_model=TripOut, status_code=201, dependencies=[Depends(ai_rate_limit)])
def create_trip(body: TripCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 1: compute the optimal visiting order, distances and the road geometry."""
    try:
        return plan_trip(db, user, [s.model_dump() for s in body.stops], body.return_to_start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/trips/{trip_id}/suggestions", dependencies=[Depends(ai_rate_limit)])
def trip_suggestions(trip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 2: fetch knowledge for each stop, retrieve, and let the LLM suggest."""
    return suggest(db, user, _own_trip(db, user, trip_id))


@router.post("/trips/{trip_id}/ask", dependencies=[Depends(ai_rate_limit)])
def trip_ask(trip_id: int, body: AskRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 3: answer a question about the trip. The question is saved, preferences are learned from it."""
    return ask(db, user, _own_trip(db, user, trip_id), body.question.strip())


@router.get("/trips", response_model=list[TripSummary])
def list_trips(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Trip).where(Trip.user_id == user.id).order_by(Trip.created_at.desc()).limit(100)).all()


@router.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(trip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _own_trip(db, user, trip_id)


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(trip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    trip = _own_trip(db, user, trip_id)
    db.execute(delete(Source).where(Source.key == f"history:{user.id}:{trip.id}"))  # forget its Q&A too
    db.delete(trip)
    get_vector_store().invalidate(db)
    db.commit()


@router.get("/trips/{trip_id}/retrieval")
def debug_retrieval(trip_id: int, q: str = Query(min_length=2, max_length=300),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Shows exactly which passages the RAG step would use, with scores (for demos/debugging)."""
    trip = _own_trip(db, user, trip_id)
    profile = load_profile(db, user.id)
    passages = retrieve(db, f"{q} {' '.join(route_names(trip))}", profile, trip.source_ids, user.id)
    return [p.to_source(n) | {"relevance": round(p.relevance, 3), "preference_boost": round(p.boost, 3)}
            for n, p in enumerate(passages, 1)]
