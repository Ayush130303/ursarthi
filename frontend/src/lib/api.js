import axios from "axios";

export const API_URL = (process.env.REACT_APP_API_URL || "http://localhost:8000").replace(/\/$/, "");

/*
 * The access token lives in memory only (never localStorage).
 * The refresh token is an HttpOnly cookie the browser sends to /auth/*.
 */
let accessToken = null;
let onSessionChange = () => {};

export const tokenStore = {
  get: () => accessToken,
  set: (token) => { accessToken = token; },
  clear: () => { accessToken = null; },
  subscribe: (fn) => { onSessionChange = fn; },
};

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

// Single-flight refresh: many 401s at once trigger only one /auth/refresh.
let refreshPromise = null;
export function refreshAccessToken() {
  if (!refreshPromise) {
    refreshPromise = axios
      .post(`${API_URL}/auth/refresh`, null, { withCredentials: true })
      .then((res) => {
        accessToken = res.data.access_token;
        onSessionChange(res.data);
        return res.data;
      })
      .catch((err) => {
        accessToken = null;
        onSessionChange(null);
        throw err;
      })
      .finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    const isAuthCall = original?.url?.startsWith("/auth/");
    if (error.response?.status === 401 && !original._retried && !isAuthCall && accessToken) {
      original._retried = true;
      await refreshAccessToken();
      return api(original);
    }
    return Promise.reject(error);
  }
);

export function errorMessage(error, fallback = "Something went wrong. Please try again.") {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg.replace(/^Value error, /, "");
  if (error?.response?.status === 429) return "You're going a bit fast. Wait a moment and try again.";
  if (!error?.response) return "Can't reach the server. Check that the API is running.";
  return fallback;
}

export default api;
