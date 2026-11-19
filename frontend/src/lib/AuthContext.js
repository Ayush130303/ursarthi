import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import api, { refreshAccessToken, tokenStore } from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const applySession = useCallback((data) => {
    tokenStore.set(data.access_token);
    setUser(data.user);
  }, []);

  const clearSession = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  useEffect(() => {
    // Interceptor-driven refreshes keep React state in sync.
    tokenStore.subscribe((data) => (data ? setUser(data.user) : setUser(null)));
    let active = true;
    refreshAccessToken()
      .catch(() => {})
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  const login = useCallback(async (email, password) => {
    const res = await api.post("/auth/login", { email, password });
    applySession(res.data);
    return res.data.user;
  }, [applySession]);

  const register = useCallback(async (name, email, password) => {
    const res = await api.post("/auth/register", { name, email, password });
    applySession(res.data);
    return res.data.user;
  }, [applySession]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } finally { clearSession(); }
  }, [clearSession]);

  const updateUser = useCallback((patch) => setUser((u) => (u ? { ...u, ...patch } : u)), []);

  const value = useMemo(() => ({
    user, loading, isAuthenticated: Boolean(user), login, register, logout, updateUser,
  }), [user, loading, login, register, logout, updateUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
