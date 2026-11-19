"""Database models.

Auth tables keep their Phase-1 names (users, refresh_tokens, password_reset_tokens)
so existing accounts keep working. Every new table is prefixed `sarthi_` so it
never collides with tables already in the database.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ auth ----

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ----------------------------------------------------------- preferences ----

class Preference(Base):
    """What the user told us in the preferences form."""

    __tablename__ = "sarthi_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    budget: Mapped[str] = mapped_column(String(20), default="medium")      # low | medium | high
    pace: Mapped[str] = mapped_column(String(20), default="balanced")      # relaxed | balanced | packed
    travel_with: Mapped[str] = mapped_column(String(20), default="solo")   # solo | couple | family | friends
    food: Mapped[str] = mapped_column(String(30), default="anything")      # anything | vegetarian | vegan | ...
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Memory(Base):
    """A preference learned automatically from the user's questions."""

    __tablename__ = "sarthi_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))      # interest | avoid | food | budget | pace | travel_with | access
    value: Mapped[str] = mapped_column(String(120))
    evidence: Mapped[str] = mapped_column(String(300))  # the sentence it was learned from
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ trips ----

class Trip(Base):
    __tablename__ = "sarthi_trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    stops: Mapped[list] = mapped_column(JSON)          # [{name, lat, lon}] in the user's order; stops[0] = start
    order: Mapped[list] = mapped_column(JSON)          # optimised visiting order (indexes into stops)
    legs_km: Mapped[list] = mapped_column(JSON)
    total_km: Mapped[float] = mapped_column(Float)
    distance_mode: Mapped[str] = mapped_column(String(20))   # road | estimated
    geometry: Mapped[list] = mapped_column(JSON)       # [[lon, lat], ...]
    source_ids: Mapped[list] = mapped_column(JSON, default=list)  # knowledge sources for this trip
    suggestions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    messages = relationship("TripMessage", cascade="all, delete-orphan", order_by="TripMessage.id",
                            passive_deletes=True, back_populates="trip")


class TripMessage(Base):
    __tablename__ = "sarthi_trip_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("sarthi_trips.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))      # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    trip = relationship("Trip", back_populates="messages")


# -------------------------------------------------------- knowledge (RAG) ----

class Source(Base):
    """A document in the knowledge base: a Wikipedia page, a Wikivoyage guide,
    or a user's own Q&A history (owner_id set, private to that user)."""

    __tablename__ = "sarthi_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(300), unique=True, index=True)  # e.g. wikipedia:12345
    kind: Mapped[str] = mapped_column(String(20), index=True)              # wikipedia | wikivoyage | history
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks = relationship("Chunk", cascade="all, delete-orphan", passive_deletes=True, back_populates="source")


class Chunk(Base):
    __tablename__ = "sarthi_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sarthi_sources.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    section: Mapped[str] = mapped_column(String(120), default="Overview")
    category: Mapped[str] = mapped_column(String(40), default="overview")
    content: Mapped[str] = mapped_column(Text)

    source = relationship("Source", back_populates="chunks")


class PlaceIndex(Base):
    """Cache: which sources were fetched around a coordinate (~1 km grid cell)."""

    __tablename__ = "sarthi_place_index"

    cell: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChunkEmbedding(Base):
    """Embeddings for the numpy backend (pgvector uses sarthi_vectors)."""

    __tablename__ = "sarthi_embeddings"

    chunk_id: Mapped[int] = mapped_column(ForeignKey("sarthi_chunks.id", ondelete="CASCADE"), primary_key=True)
    embedding: Mapped[list] = mapped_column(JSON)


class Meta(Base):
    __tablename__ = "sarthi_meta"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(300))
