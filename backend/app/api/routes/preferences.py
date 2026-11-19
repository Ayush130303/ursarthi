from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Memory, Preference, User
from app.rag.memory import SINGLE
from app.schemas import INTERESTS, MemoryOut, PreferenceIn, PreferenceOut

router = APIRouter(prefix="/preferences", tags=["Preferences"])


@router.get("/options")
def options():
    return {"interests": INTERESTS}


@router.get("", response_model=PreferenceOut | None)
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.get(Preference, user.id)


@router.put("", response_model=PreferenceOut)
def save_preferences(body: PreferenceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = db.get(Preference, user.id) or Preference(user_id=user.id)
    data = body.model_dump()
    data["interests"] = [i for i in dict.fromkeys(data["interests"]) if i in INTERESTS]
    for k, v in data.items():
        setattr(pref, k, v)
    db.add(pref)
    # The form is now the newest statement for single-valued preferences.
    for m in db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True), Memory.kind.in_(SINGLE))):
        m.active = False
    db.commit()
    return pref


@router.get("/memories", response_model=list[MemoryOut])
def memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.active.is_(True)).order_by(Memory.id.desc())).all()


@router.delete("/memories/{memory_id}", status_code=204)
def forget(memory_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(Memory, memory_id)
    if not m or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    m.active = False
    db.commit()
