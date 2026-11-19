import { useEffect, useState } from "react";
import { Alert } from "../components/common";
import api, { errorMessage } from "../lib/api";
import { useAuth } from "../lib/AuthContext";

const EMPTY = { interests: [], budget: "medium", pace: "balanced", travel_with: "solo", food: "anything", notes: "" };
const KIND_LABEL = { interest: "Likes", avoid: "Avoids", food: "Food", budget: "Budget", pace: "Pace", travel_with: "Travels with", access: "Accessibility" };

export default function Preferences() {
  const { updateUser } = useAuth();
  const [form, setForm] = useState(EMPTY);
  const [options, setOptions] = useState([]);
  const [memories, setMemories] = useState([]);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  const loadMemories = () => api.get("/preferences/memories").then((r) => setMemories(r.data)).catch(() => {});
  useEffect(() => {
    api.get("/preferences/options").then((r) => setOptions(r.data.interests));
    api.get("/preferences").then((r) => r.data && setForm({ ...EMPTY, ...r.data, notes: r.data.notes || "" }));
    loadMemories();
  }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const toggle = (i) => setForm({ ...form, interests: form.interests.includes(i) ? form.interests.filter((x) => x !== i) : [...form.interests, i] });

  const save = async (e) => {
    e.preventDefault();
    try {
      await api.put("/preferences", { ...form, notes: form.notes || null });
      updateUser({ has_preferences: true });
      setMsg({ ok: "Preferences saved. They will be used in your next suggestions and answers.", err: "" });
      loadMemories();
    } catch (err) { setMsg({ ok: "", err: errorMessage(err) }); }
  };

  const forget = async (id) => {
    await api.delete(`/preferences/memories/${id}`).catch(() => {});
    setMemories((m) => m.filter((x) => x.id !== id));
  };

  return (
    <div className="page two-col">
      <form className="card" onSubmit={save}>
        <h2>My preferences</h2>
        <p className="muted small">urSarthi uses these to pick which information to show you first.</p>
        <fieldset>
          <legend>Interests</legend>
          <div className="checks">
            {options.map((i) => (
              <label key={i} className={form.interests.includes(i) ? "pill on" : "pill"}>
                <input type="checkbox" checked={form.interests.includes(i)} onChange={() => toggle(i)} /> {i}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid2">
          <label>Budget<select value={form.budget} onChange={set("budget")}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
          <label>Pace<select value={form.pace} onChange={set("pace")}><option value="relaxed">Relaxed</option><option value="balanced">Balanced</option><option value="packed">Packed</option></select></label>
          <label>Travelling with<select value={form.travel_with} onChange={set("travel_with")}><option value="solo">Solo</option><option value="couple">Partner</option><option value="family">Family</option><option value="friends">Friends</option></select></label>
          <label>Food<select value={form.food} onChange={set("food")}>{["anything", "vegetarian", "vegan", "jain", "halal", "non-vegetarian"].map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
        </div>
        <label>Anything else?<textarea rows={3} value={form.notes} onChange={set("notes")} maxLength={500} placeholder="e.g. I get car sick on long mountain roads" /></label>
        <Alert type="success">{msg.ok}</Alert>
        <Alert>{msg.err}</Alert>
        <button className="btn btn-primary">Save preferences</button>
      </form>

      <div className="card">
        <h2>Learned from your questions</h2>
        <p className="muted small">When you mention things like "I'm vegetarian" or "I love monasteries" while asking questions, urSarthi remembers them for future trips.</p>
        {memories.length === 0 ? <p className="muted">Nothing learned yet. Ask a few questions on a trip.</p> : (
          <ul className="memories">
            {memories.map((m) => (
              <li key={m.id}>
                <div><b>{KIND_LABEL[m.kind] || m.kind}:</b> {m.value}<p className="muted small">From: "{m.evidence}"</p></div>
                <button className="btn btn-outline btn-sm" onClick={() => forget(m.id)}>Forget</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
