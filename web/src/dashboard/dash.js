import { useCallback, useState } from "react";

// Dashboard requests ride on the session cookie. Every POST carries the CSRF header; writes
// that create money movements also carry an Idempotency-Key (see useIdempotencyKey).
async function send(path, init = {}) {
  try {
    const res = await fetch(path, { credentials: "same-origin", ...init });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  } catch {
    return { status: 0, body: {} };
  }
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
  if (status === 0) return "Couldn't reach the server. Please try again.";
  return body.error?.message ?? "Something went wrong. Please try again.";
}

// One key per rendered action: a double click replays the first request; once a response
// arrives, renew() hands out a fresh key so a deliberate second action isn't a replay.
export function useIdempotencyKey() {
  const [key, setKey] = useState(() => crypto.randomUUID());
  const renew = useCallback(() => setKey(crypto.randomUUID()), []);
  return [key, renew];
}
