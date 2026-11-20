import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { RETRY_MESSAGE, postForm } from "./api.js";

// Read the secret once, then drop it from the address bar (Referrer-Policy already keeps
// it out of Referer headers).
const secret = new URLSearchParams(window.location.search).get("client_secret");
if (secret) window.history.replaceState(null, "", window.location.pathname);

export default function ThreeDS() {
  const { pi } = useParams();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Back/forward cache restores the page mid-submit: re-enable the buttons.
    function onShow(event) {
      if (event.persisted) setBusy(false);
    }
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  async function finish(result) {
    setBusy(true);
    setError(null);
    const { status, body } = await postForm(`/checkout-api/3ds/${encodeURIComponent(pi)}`, {
      client_secret: secret ?? "",
      result,
    });
    if (status === 200 && body.redirect) return window.location.assign(body.redirect);
    setError(status === 0 ? RETRY_MESSAGE : (body.error?.message ?? RETRY_MESSAGE));
    setBusy(false);
  }

  if (!secret) {
    // A reload after replaceState, or a hand-typed URL: the one-time link is spent.
    return (
      <main className="page">
        <p>This authentication link has already been used. Go back to the checkout page to
          continue.</p>
      </main>
    );
  }

  return (
    <main className="page">
      <h1>Confirm your payment</h1>
      <p>Your bank is asking you to authenticate this payment (simulated 3D Secure).</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="row">
        <button type="button" disabled={busy} onClick={() => finish("approve")}>
          Complete authentication
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={() => finish("fail")}>
          Fail authentication
        </button>
      </div>
    </main>
  );
}
