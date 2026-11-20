// Form-encoded POSTs, like the rest of the API. Returns {status, body}.
export async function postForm(path, fields) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

export async function getJson(path) {
  const res = await fetch(path);
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

export function money(amount, currency) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount / 100);
}
