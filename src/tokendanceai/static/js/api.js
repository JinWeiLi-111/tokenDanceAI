/**
 * API client — 统一封装 token、请求头、错误处理，覆盖全部已就绪 endpoint。
 * 契约来源：src/tokendanceai/router/*.py 与 schemas/*.py
 */

const TOKEN_KEY = "td-token";
const PROFILE_KEY = "td-profile";

/* ---------- token ---------- */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(PROFILE_KEY);
}

/* ---------- profile (展示用，无 /me endpoint，登录后仅知 email) ---------- */
export function getProfile() {
  try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) ?? null; }
  catch { return null; }
}
export function setProfile(p) {
  localStorage.setItem(PROFILE_KEY, JSON.stringify(p));
}

/* ---------- core request ---------- */
function buildQuery(params) {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/**
 * 通用请求。401 时清 token 并跳登录页。
 * @returns parsed JSON（或 null for 204）
 */
export async function request(method, path, { body, query, raw } = {}) {
  const headers = { "Accept": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let payload;
  if (body instanceof FormData) {
    payload = body; // fetch 自动设置 multipart/form-data
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(`/api${path}${buildQuery(query)}`, {
    method,
    headers,
    body: payload,
  });

  if (res.status === 204) return null;

  if (res.status === 401) {
    // 不再强制跳登录页：清掉失效 token，抛错由调用方决定如何提示
    clearToken();
    const err = new Error("未登录或登录已过期");
    err.status = 401;
    throw err;
  }

  let data;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); }
    catch { data = text; }
  }
  if (!res.ok) {
    const detail = (data && data.detail) ? data.detail : `请求失败 (${res.status})`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return raw ? res : data;
}

/* ---------- auth ---------- */
// 注册：POST /api/user，body = {username, email, password}
export function register(payload) {
  return request("POST", "/user", { body: payload });
}

// 登录：POST /api/user/login，OAuth2PasswordRequestForm
// 后端把 form 的 username 字段当 email 用（见 router/user.py 注释）
export function login({ email, password }) {
  const form = new FormData();
  form.set("username", email);
  form.set("password", password);
  return request("POST", "/user/login", { body: form });
}

/* ---------- sessions ---------- */
export function listSessions(limit = 50, offset = 0) {
  return request("GET", "/sessions", { query: { limit, offset } });
}
export function listMessages(sessionId, limit = 100, offset = 0) {
  return request("GET", `/sessions/${sessionId}/messages`, { query: { limit, offset } });
}
export function deleteSession(id) {
  return request("DELETE", `/sessions/${id}`);
}

/* ---------- servers (MCP) ---------- */
export function listServers() {
  return request("GET", "/servers");
}
export function createServer(payload) {
  return request("POST", "/servers", { body: payload });
}
export function updateServer(id, payload) {
  return request("PATCH", `/servers/${id}`, { body: payload });
}
export function deleteServer(id) {
  return request("DELETE", `/servers/${id}`);
}
export function testServer(id) {
  return request("POST", `/servers/${id}/test`);
}

/* ---------- account (LLM config) ---------- */
export function getAccount() {
  return request("GET", "/account");
}
export function upsertAccount(payload) {
  return request("POST", "/account", { body: payload });
}

/* ---------- agent run (SSE) ---------- */
/**
 * 发起 agent run。返回原始 Response，交给 sse.streamSSE 解析。
 * 不走 request()，因为我们要拿到流而不是 JSON。
 */
export async function agentRun({ message, session_id, autopoilt }) {
  const token = getToken();
  const res = await fetch("/api/agent/run", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
      ...(token ? { "Authorization": `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, session_id, autopoilt }),
  });
  if (res.status === 401) {
    clearToken();
    const err = new Error("未登录或登录已过期");
    err.status = 401;
    throw err;
  }
  return res;
}
