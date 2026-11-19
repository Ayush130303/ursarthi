import itertools
import random

from app.rag import llm as llm_module
from app.rag.memory import extract_rules
from app.services import tsp
from tests.conftest import FETCH_COUNT, GANGTOK_TRIP, FakeLLM, make_user

# ------------------------------------------------------------------ routing --


def _brute(m):
    n = len(m)
    return min(tsp.route_length(m, [0, *p]) for p in itertools.permutations(range(1, n)))


def test_held_karp_matches_brute_force():
    for seed in range(10):
        r = random.Random(seed)
        pts = [(r.random(), r.random()) for _ in range(7)]
        m = [[((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for b in pts] for a in pts]
        route, dist = tsp.solve(m)
        assert route[0] == 0 and sorted(route) == list(range(7)) and abs(dist - _brute(m)) < 1e-9


def test_create_trip_orders_stops(client):
    h = make_user(client)
    r = client.post("/trips", json=GANGTOK_TRIP, headers=h)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["order"][0] == 0 and sorted(t["order"]) == [0, 1, 2, 3]
    from app.services.routing import ROAD_FACTOR, haversine_m
    pts = GANGTOK_TRIP["stops"]
    m = [[haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) * ROAD_FACTOR for b in pts] for a in pts]
    assert abs(t["total_km"] - round(_brute(m) / 1000, 2)) < 0.01   # truly the shortest order
    assert t["distance_mode"] == "estimated" and len(t["legs_km"]) == 3


def test_trip_validation(client):
    h = make_user(client)
    assert client.post("/trips", json={"stops": [GANGTOK_TRIP["stops"][0]]}, headers=h).status_code == 422


# ------------------------------------------------------------ suggestions --

def test_suggestions_offline_use_rag_and_preferences(client):
    h = make_user(client, {"interests": ["spirituality"], "food": "vegetarian"})
    trip = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    s = client.post(f"/trips/{trip['id']}/suggestions", headers=h).json()
    assert s["generated_by"] == "extractive" and s["tips"] and s["sources"]
    kinds = {src["kind"] for src in s["sources"]}
    assert "wikivoyage" in kinds or "wikipedia" in kinds
    rec_names = [r["name"] for r in s["recommendations"]]
    assert "Banjhakri Falls" in rec_names or "MG Marg" in rec_names   # nearby, not already a stop
    assert not set(rec_names) & {"Gangtok", "Rumtek Monastery", "Tsomgo Lake", "Enchey Monastery"}
    assert all("lat" in r and "lon" in r for r in s["recommendations"])  # user can add them to the route
    # suggestions are saved with the trip
    assert client.get(f"/trips/{trip['id']}", headers=h).json()["suggestions"]["summary"] == s["summary"]


def test_knowledge_is_cached_per_place(client):
    h = make_user(client)
    trip = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    client.post(f"/trips/{trip['id']}/suggestions", headers=h)
    before = FETCH_COUNT["n"]
    trip2 = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    client.post(f"/trips/{trip2['id']}/suggestions", headers=h)
    assert FETCH_COUNT["n"] == before  # same places -> zero new HTTP calls


def test_llm_suggestions_drop_hallucinated_places(client):
    llm_module.set_llm_for_tests(FakeLLM())
    h = make_user(client)
    trip = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    s = client.post(f"/trips/{trip['id']}/suggestions", headers=h).json()
    assert s["generated_by"] == "llm" and s["summary"] == "A monastery loop."
    assert len(s["recommendations"]) == 1 and s["recommendations"][0]["reason"] == "fits you"


# -------------------------------------------------------------------- ask --

def test_ask_saves_history_and_learns_preferences(client):
    h = make_user(client)
    trip = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    r = client.post(f"/trips/{trip['id']}/ask", headers=h,
                    json={"question": "I am vegetarian and I love monasteries. Where should I eat in Gangtok?"}).json()
    assert "[1]" in r["answer"] and r["sources"]
    assert {(m["kind"], m["value"]) for m in r["learned"]} >= {("food", "vegetarian"), ("interest", "monasteries")}
    mems = client.get("/preferences/memories", headers=h).json()
    assert {m["value"] for m in mems} >= {"vegetarian", "monasteries"}
    detail = client.get(f"/trips/{trip['id']}", headers=h).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_past_questions_are_recalled_on_a_new_trip(client):
    h = make_user(client)
    t1 = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    client.post(f"/trips/{t1['id']}/ask", headers=h, json={"question": "Is the Tsomgo Lake permit needed for Indian citizens?"})
    t2 = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    hits = client.get(f"/trips/{t2['id']}/retrieval", params={"q": "permit for Tsomgo Lake"}, headers=h).json()
    assert any(x["kind"] == "history" for x in hits)


def test_history_is_private(client):
    a = make_user(client)
    t = client.post("/trips", json=GANGTOK_TRIP, headers=a).json()
    client.post(f"/trips/{t['id']}/ask", headers=a, json={"question": "My secret code word is zebrafalcon, remember it"})
    b = make_user(client)
    tb = client.post("/trips", json=GANGTOK_TRIP, headers=b).json()
    hits = client.get(f"/trips/{tb['id']}/retrieval", params={"q": "zebrafalcon code word"}, headers=b).json()
    assert not any("zebrafalcon" in x["snippet"] for x in hits)
    assert client.get(f"/trips/{t['id']}", headers=b).status_code == 404


def test_ask_with_llm_and_llm_memory_extraction(client):
    fake = FakeLLM()
    llm_module.set_llm_for_tests(fake)
    h = make_user(client, {"interests": ["nature"]})
    t = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    r = client.post(f"/trips/{t['id']}/ask", headers=h, json={"question": "Any kayak spots? Kayak trips are my thing."}).json()
    assert r["used_llm"] and r["answer"] == "Grounded answer [1]."
    assert ("interest", "kayaking") in {(m["kind"], m["value"]) for m in r["learned"]}
    prompt = fake.calls[-1][-1]["content"]
    assert "nature" in prompt and "kayaking" in prompt and "CONTEXT" in prompt


def test_memory_conflicts_and_form_override(client):
    h = make_user(client)
    t = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    client.post(f"/trips/{t['id']}/ask", headers=h, json={"question": "I am vegetarian, food ideas?"})
    client.post(f"/trips/{t['id']}/ask", headers=h, json={"question": "Actually I eat meat now, what's good?"})
    values = {(m["kind"], m["value"]) for m in client.get("/preferences/memories", headers=h).json()}
    assert ("food", "non-vegetarian") in values and ("food", "vegetarian") not in values
    client.put("/preferences", json={"food": "vegan"}, headers=h)   # form is newer: food memory retired
    assert not [m for m in client.get("/preferences/memories", headers=h).json() if m["kind"] == "food"]


def test_delete_trip_forgets_its_questions(client):
    h = make_user(client)
    t = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    client.post(f"/trips/{t['id']}/ask", headers=h, json={"question": "Where can I find quokkaberry jam?"})
    assert client.delete(f"/trips/{t['id']}", headers=h).status_code == 204
    t2 = client.post("/trips", json=GANGTOK_TRIP, headers=h).json()
    hits = client.get(f"/trips/{t2['id']}/retrieval", params={"q": "quokkaberry jam"}, headers=h).json()
    assert not any(x["kind"] == "history" for x in hits)


def test_rule_extractor():
    assert extract_rules("I hate long bus rides, travelling with my kids") == [("avoid", "long bus rides"), ("travel_with", "family")]
    assert extract_rules("I love it") == []


# ------------------------------------------------------------ place search --

def test_place_search_falls_back_to_photon(client, monkeypatch):
    from app.core.cache import get_cache

    get_cache().clear()

    def fake(url, params=None, timeout=12):
        if "nominatim" in url:
            return None  # Nominatim down / blocked
        return {"features": [{"properties": {"name": "Gangtok", "state": "Sikkim", "country": "India", "osm_value": "city"},
                              "geometry": {"coordinates": [88.6065, 27.3389]}}]}

    monkeypatch.setattr("app.services.geo.http_get_json", fake)
    r = client.get("/places/search", params={"q": "gangtok"}).json()
    assert r[0]["name"] == "Gangtok" and r[0]["address"] == "Gangtok, Sikkim, India"


def test_place_search_reports_outage(client, monkeypatch):
    from app.core.cache import get_cache

    get_cache().clear()
    monkeypatch.setattr("app.services.geo.http_get_json", lambda *a, **k: None)
    r = client.get("/places/search", params={"q": "gangtok"})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]
