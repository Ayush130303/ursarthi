# urSarthi – route planner with a RAG travel assistant

urSarthi ("your guide") lets a user add several places, computes the **shortest visiting order**, then uses **Retrieval-Augmented Generation (RAG)** to give suggestions and answer questions about the trip. Every question is saved, and the preferences a user mentions ("I'm vegetarian", "I love monasteries") are **learned automatically** and used in future searches.

**Stack:** FastAPI · PostgreSQL (pgvector optional) · SQLAlchemy · Groq LLM (OpenAI-compatible) · React · Leaflet

## How it works

```
User adds places ──► POST /trips
                      1. distance matrix (OpenRouteService road distance, or haversine × 1.3)
                      2. shortest order: Held–Karp DP (exact, ≤11 stops) / nearest-neighbour + 2-opt
                      3. route geometry saved as a Trip

                 ──► POST /trips/{id}/suggestions
                      4. knowledge for each stop (cached per ~1 km cell, parallel HTTP):
                           • Wikipedia geosearch: articles physically near the stop
                           • the stop's Wikipedia page + Wikivoyage guide (See / Do / Eat / Stay safe)
                         → chunk → embed → store in vector store
                      5. hybrid retrieval + preference re-ranking (see below)
                      6. LLM writes summary + tips with [n] citations and picks nearby places
                         ONLY from a validated candidate list (hallucinated places are dropped)

                 ──► POST /trips/{id}/ask
                      7. learn preferences from the question (regex rules + LLM extraction)
                      8. retrieve from trip knowledge + the user's own past Q&A
                      9. LLM answer with citations; Q&A saved as private memory for future trips
```

### Retrieval (`backend/app/rag/retriever.py`)
1. **Dense search:** cosine similarity on embeddings (pgvector HNSW index, or numpy).
2. **Keyword search:** PostgreSQL full-text search (or BM25 when pgvector isn't installed).
3. **History search:** the user's own earlier questions and answers (private to that user).
4. **Reciprocal Rank Fusion** merges the ranked lists by rank position, so different score scales combine safely.
5. **Preference re-rank:** boost passages that match the user's interests or food preference; penalise things they avoid.
6. **MMR (Maximal Marginal Relevance)** picks the top passages while avoiding near-duplicates.

### Preference memory (`backend/app/rag/memory.py`)
* Extraction: regex rules (instant, offline) plus LLM JSON extraction (catches other phrasings).
* Conflict resolution: single-value facts (food, budget, pace, travel_with) keep the **newest** value. Learning "avoid X" cancels "likes X". Saving the preferences form overrides older learned values.
* The profile used everywhere = preferences form + active learned memories. Users can see and delete what was learned.

### Reliability
* Place search tries **Nominatim**, then falls back to **Photon**. If both fail, the API returns a clear 503 and the UI shows it. (Earlier, errors were silently swallowed, which is why searching "gangtok" showed nothing.)
* No LLM key, or the LLM is down? Answers are assembled from the retrieved sources (extractive fallback). A 60-second circuit breaker avoids slow repeated failures.
* No pgvector? An in-memory vector store is used automatically. No ORS key? Distances are estimated.

## Run it (Windows / macOS / Linux)

**Needs:** Python 3.10+, Node 18+, and PostgreSQL (your existing `hamrosikkim` database works).

### Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# backend/.env is already filled with your database URL and a JWT secret.
# Add a free Groq key (https://console.groq.com/keys) to GROQ_API_KEY for written answers.
uvicorn app.main:app --reload --port 8000
```
Run uvicorn from inside `backend/`. On startup the app creates its own `sarthi_*` tables. It never drops data, and existing accounts keep working. API docs: http://localhost:8000/docs

Optional: `pip install fastembed` for better semantic search (downloads a ~130 MB model once).

### Frontend
```bash
cd frontend
npm install
npm start                         # http://localhost:3000
```
Use `http://localhost:3000` (not 127.0.0.1) so the login cookie works.

### Tests
```bash
cd backend
pip install -r requirements-dev.txt
pytest                            # 19 tests, fully offline (simulated Wikipedia/OSM)
```
The tests cover the auth flows, route optimality against brute force, RAG suggestions, the knowledge cache (no repeat HTTP calls), dropping hallucinated places, saved questions, recall across trips, privacy between users, memory conflicts and the geocoder fallback.

## API

| Method | Endpoint | What it does |
|---|---|---|
| POST | `/auth/register` `/auth/login` `/auth/refresh` `/auth/logout` | Accounts (Argon2, JWT + rotating refresh cookie) |
| POST | `/auth/forgot-password` `/auth/reset-password` | Password reset |
| GET | `/places/search?q=` | Worldwide place search |
| POST | `/trips` | Optimise route for the given stops (stops[0] = start) |
| POST | `/trips/{id}/suggestions` | RAG suggestions + nearby places |
| POST | `/trips/{id}/ask` | Ask a question (saved, preferences learned) |
| GET | `/trips` · `/trips/{id}` | History |
| DELETE | `/trips/{id}` | Delete a trip and its saved Q&A |
| GET | `/trips/{id}/retrieval?q=` | Debug: shows the passages RAG would use, with scores |
| GET/PUT | `/preferences` | Preferences form |
| GET/DELETE | `/preferences/memories` | Learned preferences |

## Project structure
```
backend/app
  api/routes/   auth.py, trips.py, preferences.py
  rag/          knowledge.py (Wikipedia/Wikivoyage ingestion), retriever.py, memory.py,
                pipeline.py (suggest / ask), vector_store.py, embeddings.py, chunking.py, llm.py, prompts.py
  services/     tsp.py (Held–Karp, 2-opt), routing.py (ORS / haversine), geo.py (Nominatim → Photon)
  core/         config, security, cache, rate limiting
  models/       SQLAlchemy tables
frontend/src
  pages/        Planner.jsx, History.jsx, Preferences.jsx, Auth.jsx
  components/   map, citations, navbar
```

`backend/drop_old_tables.sql` optionally removes tables left over from the earlier VocalForLocal version.

Content: Wikipedia and Wikivoyage (CC BY-SA). Maps: © OpenStreetMap contributors.
