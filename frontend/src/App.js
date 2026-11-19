import { useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Navbar, ProtectedRoute } from "./components/common";
import api from "./lib/api";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import { ForgotPassword, Login, ResetPassword, Signup } from "./pages/Auth";
import History from "./pages/History";
import Planner from "./pages/Planner";
import Preferences from "./pages/Preferences";
import "./styles.css";

function Landing() {
  const { isAuthenticated, user } = useAuth();
  const [trips, setTrips] = useState([]);
  useEffect(() => {
    if (isAuthenticated) api.get("/trips").then((r) => setTrips(r.data.slice(0, 5))).catch(() => {});
  }, [isAuthenticated]);

  return (
    <div className="landing">
      <section className="hero">
        <h1>{isAuthenticated ? `Welcome back, ${user.name.split(" ")[0]}` : "urSarthi"}</h1>
        <p className="lead">Your travel companion. Add the places you want to visit, get the shortest route,
          and ask an AI guide that remembers what you like.</p>
        <div className="row center">
          {isAuthenticated ? (
            <>
              <Link to="/plan" className="btn btn-primary">Plan a new trip</Link>
              <Link to="/preferences" className="btn btn-outline">My preferences</Link>
            </>
          ) : (
            <>
              <Link to="/signup" className="btn btn-primary">Get started</Link>
              <Link to="/login" className="btn btn-outline">Login</Link>
            </>
          )}
        </div>
      </section>
      {isAuthenticated && trips.length > 0 && (
        <section className="card recent">
          <h3>Your recent trips</h3>
          <ul>
            {trips.map((t) => (
              <li key={t.id}>
                <Link to={`/plan/${t.id}`}>{t.title}</Link>
                <span className="muted small">{t.total_km} km, {new Date(t.created_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
          <Link to="/history" className="small">See all trips</Link>
        </section>
      )}
      <section className="features">
        <div className="card"><h3>1. Add your places</h3><p>Search any place in the world and add as many stops as you like.</p></div>
        <div className="card"><h3>2. Get the best route</h3><p>urSarthi works out the shortest order to visit them and shows it on the map.</p></div>
        <div className="card"><h3>3. Ask and get suggestions</h3><p>Tips and nearby places come from Wikipedia and Wikivoyage, matched to your preferences.</p></div>
      </section>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Navbar />
        <main>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/plan" element={<Planner />} />
              <Route path="/plan/:tripId" element={<Planner />} />
              <Route path="/history" element={<History />} />
              <Route path="/preferences" element={<Preferences />} />
            </Route>
            <Route path="*" element={<p className="center">Page not found. <Link to="/">Go home</Link></p>} />
          </Routes>
        </main>
      </AuthProvider>
    </BrowserRouter>
  );
}