import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, CitedText, SourceList, TripMap } from "../components/common";
import api, { errorMessage } from "../lib/api";

function PlaceSearch({ onPick }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState("");   // "", searching, empty, or an error message

  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); setStatus(""); return undefined; }
    const timer = setTimeout(async () => {
      setStatus("searching");
      try {
        const { data } = await api.get("/places/search", { params: { q } });
        setResults(data);
        setStatus(data.length ? "" : "empty");
      } catch (err) {
        setResults([]);
        setStatus(errorMessage(err));
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [q]);

  const pick = (p) => { onPick({ name: p.name, lat: p.lat, lon: p.lon, address: p.address }); setQ(""); setResults([]); };

  return (
    <div className="search">
      <label htmlFor="place">Add a place</label>
      <input id="place" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Gangtok, Rumtek Monastery" autoComplete="off" />
      {status === "searching" && <p className="muted small">Searching…</p>}
      {status === "empty" && <p className="muted small">No places found. Try another spelling or add the city name.</p>}
      {status && status !== "searching" && status !== "empty" && <Alert>{status}</Alert>}
      {results.length > 0 && (
        <ul className="results">
          {results.map((p, i) => (
            <li key={i}><button type="button" onClick={() => pick(p)}><b>{p.name}</b><span>{p.address}</span></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Suggestions({ data, loading, error, onAdd }) {
  if (loading) return <div className="card"><h3>AI suggestions</h3><p className="muted">Reading about your places and preparing suggestions… (first time can take ~10 s)</p></div>;
  if (error) return <div className="card"><h3>AI suggestions</h3><Alert>{error}</Alert></div>;
  if (!data) return null;
  return (
    <div className="card">
      <h3>AI suggestions {data.generated_by === "extractive" && <span className="tag">from guides (LLM offline)</span>}</h3>
      <p>{data.summary}</p>
      {data.tips?.length > 0 && <><h4>Tips</h4><CitedText text={data.tips.map((t) => `- ${t}`).join("\n")} /></>}
      {data.recommendations?.length > 0 && (
        <>
          <h4>Nearby places you may like</h4>
          <ul className="recs">
            {data.recommendations.map((r) => (
              <li key={r.id}>
                <div><b>{r.name}</b> <span className="muted small">{r.km_from_route} km from route</span><p className="small">{r.reason}</p></div>
                <button className="btn btn-outline btn-sm" onClick={() => onAdd(r)}>+ Add</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <SourceList sources={data.sources} />
    </div>
  );
}

function Chat({ tripId, messages, setMessages }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [learned, setLearned] = useState([]);
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [messages]);

  const send = async (e) => {
    e.preventDefault();
    const question = q.trim();
    if (!question) return;
    setBusy(true); setError(""); setLearned([]);
    setMessages((m) => [...m, { role: "user", content: question }]);
    setQ("");
    try {
      const { data } = await api.post(`/trips/${tripId}/ask`, { question });
      setMessages((m) => [...m, { role: "assistant", content: data.answer, sources: data.sources }]);
      setLearned(data.learned);
    } catch (err) {
      setError(errorMessage(err));
      setMessages((m) => m.slice(0, -1));
      setQ(question);
    } finally { setBusy(false); }
  };

  return (
    <div className="card">
      <h3>Ask about this trip</h3>
      <div className="chat">
        {messages.length === 0 && <p className="muted small">Try: "Where can I eat vegetarian food near the first stop?" or "Is a permit needed for Tsomgo Lake?"</p>}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "user" ? <p>{m.content}</p> : <><CitedText text={m.content} /><SourceList sources={m.sources} /></>}
          </div>
        ))}
        {busy && <div className="msg assistant"><p className="muted">Thinking…</p></div>}
        <div ref={end} />
      </div>
      {learned.length > 0 && (
        <Alert type="success">Saved to your preferences: {learned.map((l) => `${l.value} (${l.kind})`).join(", ")}</Alert>
      )}
      <Alert>{error}</Alert>
      <form onSubmit={send} className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question…" maxLength={1000} />
        <button className="btn btn-primary" disabled={busy || !q.trim()}>Ask</button>
      </form>
    </div>
  );
}

export default function Planner() {
  const { tripId } = useParams();
  const navigate = useNavigate();
  const [stops, setStops] = useState([]);
  const [useLocation, setUseLocation] = useState(true);
  const [myLoc, setMyLoc] = useState(null);
  const [trip, setTrip] = useState(null);
  const [routeError, setRouteError] = useState("");
  const [routing, setRouting] = useState(false);
  const [sugg, setSugg] = useState(null);
  const [suggLoading, setSuggLoading] = useState(false);
  const [suggError, setSuggError] = useState("");
  const [messages, setMessages] = useState([]);
  const [note, setNote] = useState("");
  const createdId = useRef(null);

  useEffect(() => {
    navigator.geolocation?.getCurrentPosition(
      (p) => setMyLoc({ name: "My location", lat: p.coords.latitude, lon: p.coords.longitude }),
      () => setUseLocation(false), { timeout: 8000 });
  }, []);

  const loadSuggestions = async (id) => {
    setSuggLoading(true); setSuggError("");
    try { setSugg((await api.post(`/trips/${id}/suggestions`)).data); }
    catch (err) { setSuggError(errorMessage(err)); }
    finally { setSuggLoading(false); }
  };

  // Open a saved trip from History
  useEffect(() => {
    if (!tripId) { setTrip(null); setSugg(null); setMessages([]); return; }
    if (createdId.current === Number(tripId) || trip?.id === Number(tripId)) return;
    api.get(`/trips/${tripId}`).then(({ data }) => {
      setTrip(data); setStops(data.stops); setUseLocation(false); setMessages(data.messages);
      if (data.suggestions) setSugg(data.suggestions); else loadSuggestions(data.id);
    }).catch((err) => setRouteError(errorMessage(err, "Trip not found.")));
  }, [tripId]); // eslint-disable-line react-hooks/exhaustive-deps

  const allStops = useLocation && myLoc ? [myLoc, ...stops] : stops;
  const changed = trip && JSON.stringify(trip.stops.map((s) => [s.lat, s.lon])) !== JSON.stringify(allStops.map((s) => [s.lat, s.lon]));

  const addStop = (p) => {
    if (stops.some((s) => Math.abs(s.lat - p.lat) < 1e-4 && Math.abs(s.lon - p.lon) < 1e-4)) return setNote(`${p.name} is already in your list.`);
    setStops((s) => [...s, { name: p.name, lat: p.lat, lon: p.lon }]);
    setNote(trip ? `${p.name} added. Click "Recalculate route" to include it.` : "");
  };
  const removeStop = (i) => setStops((s) => s.filter((_, j) => j !== i));
  const moveUp = (i) => i > 0 && setStops((s) => { const c = [...s]; [c[i - 1], c[i]] = [c[i], c[i - 1]]; return c; });

  const calculate = async () => {
    setRouteError(""); setNote("");
    if (allStops.length < 2) return setRouteError(useLocation && myLoc ? "Add at least one place." : "Add at least two places (the first one is the start).");
    setRouting(true);
    try {
      const { data } = await api.post("/trips", { stops: allStops.map(({ name, lat, lon }) => ({ name, lat, lon })) });
      setTrip(data); setSugg(null); setMessages([]);
      createdId.current = data.id;
      navigate(`/plan/${data.id}`, { replace: true });
      loadSuggestions(data.id);
    } catch (err) { setRouteError(errorMessage(err)); }
    finally { setRouting(false); }
  };

  const newTrip = () => { setStops([]); setTrip(null); setSugg(null); setMessages([]); setNote(""); setUseLocation(Boolean(myLoc)); navigate("/plan"); };

  const line = useMemo(() => (trip?.geometry || []).map(([lon, lat]) => [lat, lon]), [trip]);
  const mapStops = trip && !changed ? trip.stops : allStops;

  return (
    <div className="planner">
      <aside className="panel">
        <div className="panel-head">
          <h2>{trip ? "Your trip" : "Plan a trip"}</h2>
          {trip && <button className="btn btn-outline btn-sm" onClick={newTrip}>New trip</button>}
        </div>

        <div className="card">
          <PlaceSearch onPick={addStop} />
          {myLoc && (
            <label className="check"><input type="checkbox" checked={useLocation} onChange={(e) => setUseLocation(e.target.checked)} /> Start from my current location</label>
          )}
          {allStops.length > 0 && (
            <ol className="stops">
              {allStops.map((s, i) => {
                const own = useLocation && myLoc ? i - 1 : i;
                return (
                  <li key={`${s.lat},${s.lon},${i}`}>
                    <span><b>{i === 0 ? "Start" : `Stop ${i}`}:</b> {s.name}</span>
                    {own >= 0 && (
                      <span className="stop-actions">
                        {own > 0 && <button title="Move up" onClick={() => moveUp(own)}>↑</button>}
                        <button title="Remove" onClick={() => removeStop(own)}>✕</button>
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
          <Alert type="info">{note}</Alert>
          <Alert>{routeError}</Alert>
          <button className="btn btn-primary btn-block" onClick={calculate} disabled={routing}>
            {routing ? "Calculating…" : trip ? "Recalculate route" : "Calculate best route"}
          </button>
        </div>

        {trip && !changed && (
          <div className="card">
            <h3>Best order</h3>
            <ol className="order">
              {trip.order.map((idx, i) => (
                <li key={i}>{trip.stops[idx].name.split(",")[0]} {i > 0 && <span className="muted small">+{trip.legs_km[i - 1]} km</span>}</li>
              ))}
            </ol>
            <p><b>Total: {trip.total_km} km</b> {trip.distance_mode === "estimated" && <span className="muted small">(estimated, add an ORS key for road distance)</span>}</p>
          </div>
        )}

        {trip && <Suggestions data={sugg} loading={suggLoading} error={suggError} onAdd={addStop} />}
        {trip && <Chat tripId={trip.id} messages={messages} setMessages={setMessages} />}
      </aside>

      <section className="map">
        <TripMap stops={mapStops} order={trip && !changed ? trip.order : null} line={trip && !changed ? line : null}
          extra={(sugg?.recommendations || []).filter((r) => !stops.some((s) => s.name === r.name))} />
      </section>
    </div>
  );
}
