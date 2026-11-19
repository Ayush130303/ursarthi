import { useEffect } from "react";
import L from "leaflet";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";

export function Navbar() {
  const { user, isAuthenticated, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <header className="navbar">
      <Link to="/" className="brand">ur<span>Sarthi</span></Link>
      {isAuthenticated ? (
        <nav>
          <NavLink to="/" end>Home</NavLink>
          <NavLink to="/plan">Plan trip</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/preferences">Preferences</NavLink>
          <span className="nav-user">Hi, {user.name.split(" ")[0]}</span>
          <button className="btn btn-outline btn-sm" onClick={async () => { await logout(); navigate("/"); }}>Logout</button>
        </nav>
      ) : (
        <nav>
          <NavLink to="/login">Login</NavLink>
          <Link to="/signup" className="btn btn-primary btn-sm">Sign up</Link>
        </nav>
      )}
    </header>
  );
}

export function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="center muted">Loading…</p>;
  if (!isAuthenticated) return <Navigate to="/login" replace state={{ from: location }} />;
  return <Outlet />;
}

export function Alert({ type = "error", children }) {
  if (!children) return null;
  return <div className={`alert alert-${type}`} role={type === "error" ? "alert" : "status"}>{children}</div>;
}

/** Shows text with [1] style citations as small badges. */
export function CitedText({ text }) {
  const blocks = [];
  let items = [];
  const flush = () => { if (items.length) blocks.push(<ul key={`u${blocks.length}`}>{items}</ul>); items = []; };
  text.split("\n").filter((l) => l.trim()).forEach((line, i) => {
    const isItem = /^\s*([-*•]|\d+\.)\s+/.test(line);
    const clean = line.replace(/^\s*([-*•]|\d+\.)\s+/, "");
    const parts = clean.split(/(\[\d+\])/g).map((p, j) =>
      /^\[\d+\]$/.test(p) ? <sup key={j} className="cite">{p.slice(1, -1)}</sup> : p.replace(/\*\*/g, "").replace(/^_|_$/g, "")
    );
    if (isItem) items.push(<li key={i}>{parts}</li>);
    else { flush(); blocks.push(<p key={i}>{parts}</p>); }
  });
  flush();
  return <div className="cited">{blocks}</div>;
}

export function SourceList({ sources }) {
  if (!sources?.length) return null;
  return (
    <details className="sources">
      <summary>Sources ({sources.length})</summary>
      <ol>
        {sources.map((s) => (
          <li key={s.n}>
            <strong>[{s.n}] {s.title}</strong> <span className="muted">({s.kind === "history" ? "your earlier question" : s.kind})</span>
            <p>{s.snippet}</p>
            {s.reasons?.length > 0 && <p className="why">Why picked: {s.reasons.join(", ")}</p>}
            {s.url && <a href={s.url} target="_blank" rel="noreferrer">Read more</a>}
          </li>
        ))}
      </ol>
    </details>
  );
}

const icon = (label, cls) =>
  L.divIcon({ className: "", html: `<div class="pin ${cls}">${label}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });

function FitBounds({ points }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 12, { animate: false });
    if (points.length > 1) map.fitBounds(points, { padding: [40, 40], animate: false });
  }, [map, points]);
  return null;
}

/** stops: [{name, lat, lon}], order: optimised indexes (optional), line: [[lat, lon]], extra: suggested places */
export function TripMap({ stops, order, line, extra = [] }) {
  const ordered = order?.length ? order.map((i) => stops[i]) : stops;
  const points = [...stops, ...extra].map((s) => [s.lat, s.lon]);
  return (
    <MapContainer center={[22, 80]} zoom={4} style={{ height: "100%", width: "100%" }}>
      <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <FitBounds points={points} />
      {ordered.map((s, i) => (
        <Marker key={`s${i}`} position={[s.lat, s.lon]} icon={icon(i === 0 ? "S" : i, i === 0 ? "start" : "stop")}>
          <Popup>{i === 0 ? "Start: " : `Stop ${i}: `}{s.name}</Popup>
        </Marker>
      ))}
      {extra.map((p) => (
        <Marker key={`x${p.id}`} position={[p.lat, p.lon]} icon={icon("★", "extra")}>
          <Popup>Suggested: {p.name}</Popup>
        </Marker>
      ))}
      {line?.length > 1 && <Polyline positions={line} pathOptions={{ color: "#1d4ed8", weight: 5 }} />}
    </MapContainer>
  );
}