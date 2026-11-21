import { AlertCircle, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { RETRY_MESSAGE, postForm } from "./api.js";
import { Spinner } from "./ui.jsx";

// Read the secret once, then drop it from the address bar (Referrer-Policy already keeps
// it out of Referer headers).
const secret = new URLSearchParams(window.location.search).get("client_secret");
if (secret) window.history.replaceState(null, "", window.location.pathname);

export default function ThreeDS() {
  const { pi } = useParams();
  const [busy, setBusy] = useState(null); // "approve" | "fail" | null
  const [error, setError] = useState(null);

  useEffect(() => {
    // Back/forward cache restores the page mid-submit: re-enable the buttons.
    function onShow(event) {
      if (event.persisted) setBusy(null);
    }
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  async function finish(result) {
    setBusy(result);
    setError(null);
    const { status, body } = await postForm(`/checkout-api/3ds/${encodeURIComponent(pi)}`, {
      client_secret: secret ?? "",
      result,
    });
    if (status === 200 && body.redirect) return window.location.assign(body.redirect);
    setError(status === 0 ? RETRY_MESSAGE : (body.error?.message ?? RETRY_MESSAGE));
    setBusy(null);
  }

  if (!secret) {
    // A reload after replaceState, or a hand-typed URL: the one-time link is spent.
    return (
      <main className="center-page">
        <div className="center-card">
          <div className="status-icon neutral">
            <AlertCircle size={24} aria-hidden="true" />
          </div>
          <h1>Link already used</h1>
          <p>
            This authentication link has already been used. Go back to the checkout page to
            continue.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="center-page">
      <div className="center-card">
        <div className="status-icon brand">
          <ShieldCheck size={24} aria-hidden="true" />
        </div>
        <h1>Confirm it&apos;s you</h1>
        <p>Your bank is asking you to authenticate this payment.</p>
        <div className="bank-frame">
          <dl className="facts">
            <dt>Check</dt>
            <dd>3D Secure, simulated</dd>
            <dt>Payment</dt>
            <dd className="id">{pi}</dd>
          </dl>
        </div>
        {error && (
          <p className="alert" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            {error}
          </p>
        )}
        <div className="button-pair">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy !== null}
            onClick={() => finish("fail")}
          >
            {busy === "fail" && <Spinner />}
            Fail authentication
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy !== null}
            onClick={() => finish("approve")}
          >
            {busy === "approve" && <Spinner />}
            Complete authentication
          </button>
        </div>
      </div>
    </main>
  );
}
