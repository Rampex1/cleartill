// Form-encoded POSTs, like the rest of the API. Returns {status, body}; status 0 means the
// request never got a response (offline, reset), which callers treat as retryable.
async function request(path, init) {
  try {
    const res = await fetch(path, init);
    return { status: res.status, body: await res.json().catch(() => ({})) };
  } catch {
    return { status: 0, body: {} };
  }
}

export function postForm(path, fields) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
}

export function getJson(path) {
  return request(path);
}

export function money(amount, currency) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount / 100);
}

export const RETRY_MESSAGE = "Something went wrong. Please try again.";
