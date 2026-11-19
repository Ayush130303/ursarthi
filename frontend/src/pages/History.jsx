import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/common";
import api, { errorMessage } from "../lib/api";

export default function History() {
  const [trips, setTrips] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/trips").then((r) => setTrips(r.data)).catch((e) => { setError(errorMessage(e)); setTrips([]); });
  }, []);

  const remove = async (id) => {
    if (!window.confirm("Delete this trip and its saved questions?")) return;
    try {
      await api.delete(`/trips/${id}`);
      setTrips((t) => t.filter((x) => x.id !== id));
    } catch (e) { setError(errorMessage(e)); }
  };

  return (
    <div className="page">
      <h1>My trips</h1>
      <p className="muted">Every route you plan is saved here with its suggestions and the questions you asked.</p>
      <Alert>{error}</Alert>
      {trips === null ? <p className="muted">Loading…</p> : trips.length === 0 ? (
        <div className="card center">
          <p>You haven't planned any trips yet.</p>
          <Link to="/plan" className="btn btn-primary">Plan your first trip</Link>
        </div>
      ) : (
        <table className="table">
          <thead><tr><th>Route</th><th>Distance</th><th>Date</th><th /></tr></thead>
          <tbody>
            {trips.map((t) => (
              <tr key={t.id}>
                <td><Link to={`/plan/${t.id}`}>{t.title}</Link></td>
                <td>{t.total_km} km</td>
                <td>{new Date(t.created_at).toLocaleDateString()}</td>
                <td className="actions">
                  <Link to={`/plan/${t.id}`} className="btn btn-outline btn-sm">Open</Link>
                  <button className="btn btn-danger btn-sm" onClick={() => remove(t.id)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
