import os
import re
import tempfile
import uuid

# Isolated offline environment, set BEFORE the app is imported.
# Run against PostgreSQL+pgvector with: TEST_DATABASE_URL=postgresql+psycopg2://... pytest
_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update({
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"),
    "ENVIRONMENT": "test",
    "JWT_SECRET": "test-secret-" + "x" * 40,
    "LLM_API_KEY": "", "GROQ_API_KEY": "", "ORS_API_KEY": "", "REDIS_URL": "",
    "EMBEDDING_PROVIDER": "hash",
    "VECTOR_BACKEND": os.environ.get("TEST_VECTOR_BACKEND", "numpy"),
    "RATE_LIMIT_AI_PER_MINUTE": "1000", "RATE_LIMIT_REGISTER_PER_MINUTE": "1000",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.rag import llm as llm_module  # noqa: E402
from app.services.routing import haversine_m  # noqa: E402

# ------------------------------------------------------------ fake internet --

PAGES = {
    "Gangtok": (27.3389, 88.6065, "Gangtok is the capital of Sikkim, a hill town in the eastern Himalayas known for monasteries and views of Kanchenjunga."),
    "Enchey Monastery": (27.3425, 88.6197, "Enchey Monastery is a 200-year-old Buddhist monastery of the Nyingma order above Gangtok. Monks perform the Chaam dance festival in winter."),
    "MG Marg": (27.3297, 88.6125, "MG Marg is a pedestrian shopping street in Gangtok with cafes, momo stalls and vegetarian restaurants. It is the social heart of the town in the evening."),
    "Rumtek Monastery": (27.2886, 88.5617, "Rumtek Monastery is the largest monastery in Sikkim and seat of the Karmapa. It has a golden stupa and a monastic college."),
    "Tsomgo Lake": (27.3742, 88.7619, "Tsomgo Lake is a glacial lake at 3,753 m. Visitors need a permit and the road can close after snowfall. Yak rides are offered on the shore."),
    "Banjhakri Falls": (27.3528, 88.5891, "Banjhakri Falls is a waterfall and energy park near Gangtok popular with families, with a short walking trail."),
}
VOYAGE = ("Gangtok is the capital of Sikkim.\n== Understand ==\nGangtok sits on a ridge at 1,650 m.\n"
          "== See ==\nTashi Viewpoint offers sunrise views of Kanchenjunga on clear mornings.\n"
          "== Eat ==\nMomos and thukpa are everywhere. Many family restaurants serve vegetarian thali. Try churpi, a local hard cheese.\n"
          "== Stay safe ==\nLandslides can block roads during the monsoon from June to September; carry cash as ATMs run dry.\n"
          "== Sleep ==\nHomestays around Gangtok are cheap and friendly.")


def fake_get_json(url, params=None, timeout=12):
    params = params or {}
    if "wikivoyage" in url:
        if params.get("list") == "search":
            return {"query": {"search": [{"title": "Gangtok"}]}}
        if "Gangtok" in params.get("titles", ""):
            return {"query": {"pages": {"1": {"title": "Gangtok", "extract": VOYAGE}}}}
        return {"query": {"pages": {"-1": {"missing": ""}}}}
    if params.get("list") == "geosearch":
        lat, lon = map(float, params["gscoord"].split("|"))
        hits = [{"title": t, "lat": la, "lon": lo, "dist": haversine_m(lat, lon, la, lo)}
                for t, (la, lo, _) in PAGES.items() if haversine_m(lat, lon, la, lo) < 15000]
        return {"query": {"geosearch": hits}}
    if params.get("list") == "search":
        name = params["srsearch"].lower()
        hits = [{"title": t} for t in PAGES if t.lower().startswith(name[:5])]
        return {"query": {"search": hits[:1]}}
    if params.get("prop") == "extracts":
        titles = params["titles"].split("|")
        return {"query": {"pages": {str(i): {"title": t, "extract": PAGES[t][2]} for i, t in enumerate(titles) if t in PAGES}}}
    return None


FETCH_COUNT = {"n": 0}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def counting(*a, **k):
        FETCH_COUNT["n"] += 1
        return fake_get_json(*a, **k)
    monkeypatch.setattr("app.rag.knowledge.http_get_json", counting)
    llm_module.set_llm_for_tests(llm_module.LLMClient())  # no LLM unless a test opts in
    yield
    llm_module.set_llm_for_tests(None)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def make_user(client, prefs: dict | None = None):
    client.cookies.clear()
    r = client.post("/auth/register", json={"name": "Pema Bhutia", "email": f"{uuid.uuid4().hex[:10]}@example.com",
                                            "password": "TestPass123!"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if prefs:
        assert client.put("/preferences", json=prefs, headers=h).status_code == 200
    return h


GANGTOK_TRIP = {"stops": [
    {"name": "Gangtok", "lat": 27.3389, "lon": 88.6065},
    {"name": "Rumtek Monastery", "lat": 27.2886, "lon": 88.5617},
    {"name": "Tsomgo Lake", "lat": 27.3742, "lon": 88.7619},
    {"name": "Enchey Monastery", "lat": 27.3425, "lon": 88.6197},
]}


class FakeLLM:
    available = True
    model = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, messages, json_mode=False, max_tokens=None):
        self.calls.append(messages)
        prompt = messages[-1]["content"]
        if "LASTING travel preferences" in prompt:
            return '{"memories": [{"kind": "interest", "value": "kayaking"}]}' if "kayak" in prompt else '{"memories": []}'
        if "NEARBY PLACES" in prompt:
            ids = [int(i) for i in re.findall(r"id=(\d+):", prompt)]
            recs = [{"id": ids[0], "reason": "fits you"}] if ids else []
            recs.append({"id": 999999, "reason": "hallucinated"})
            return '{"summary": "A monastery loop.", "tips": ["Carry cash [1]"], "recommendations": %s}' % str(recs).replace("'", '"')
        return "Grounded answer [1]."
