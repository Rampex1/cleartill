import { useCallback, useRef } from "react";

const UNAUTHORIZED = "cleartill:unauthorized";

// Dashboard requests ride on the session cookie. Every POST carries the CSRF header; money
// actions also carry an Idempotency-Key (see useIdempotencyKey).
async function send(path, init = {}) {
  let res;
  try {
    res = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    return { status: 0, body: {} };
  }
  const body = await res.json().catch(() => ({}));
  // A session that ended mid-visit (expired, or logged out elsewhere): Layout sends the user
  // to login. The login form's own 401 (bad password) is an ordinary error.
  if (res.status === 401 && path !== "/dashboard-api/login") {
    window.dispatchEvent(new Event(UNAUTHORIZED));
  }
  return { status: res.status, body };
}

export function onUnauthorized(handler) {
  window.addEventListener(UNAUTHORIZED, handler);
  return () => window.removeEventListener(UNAUTHORIZED, handler);
}

export function dashGet(path) {
  return send(path);
}

export function dashPost(path, fields = {}, idempotencyKey = null) {
  const headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Cleartill-Dashboard": "1",
  };
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  return send(path, { method: "POST", headers, body: new URLSearchParams(fields) });
}

export function errorMessage({ status, body }) {
  if (status === 0) {
    return "Couldn't reach the server. The action may have gone through; retrying is safe.";
  }
  if (body.error?.code === "idempotency_key_reused") {
    return "The previous attempt may have gone through. Reload the page to check.";
  }
  return body.error?.message ?? "Something went wrong. Please try again.";
}

// Design §5.6: one key per action. A double submit, or a retry after a lost response
// (status 0) with the same fields, reuses the key, so the server replays instead of acting
// twice. Once a response arrives, or the fields change, the next submit gets a fresh key.
export function useIdempotencyKey() {
  const state = useRef({ key: crypto.randomUUID(), fields: null });
  const keyFor = useCallback((fields) => {
    const signature = JSON.stringify(fields);
    if (state.current.fields !== null && state.current.fields !== signature) {
      state.current = { key: crypto.randomUUID(), fields: null };
    }
    state.current.fields = signature;
    return state.current.key;
  }, []);
  const settle = useCallback((status) => {
    if (status !== 0) state.current = { key: crypto.randomUUID(), fields: null };
  }, []);
  return [keyFor, settle];
}
