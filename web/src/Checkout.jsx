import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { RETRY_MESSAGE, getJson, money, postForm } from "./api.js";

const TEST_CARDS = [
  ["4242 4242 4242 4242", "succeeds"],
  ["4000 0000 0000 0002", "is declined"],
  ["4000 0027 6000 3184", "asks for 3D Secure"],
];

export default function Checkout() {
  const { cs } = useParams();
  const [session, setSession] = useState(null);
  const [loadError, setLoadError] = useState(null); // "missing" | "retry"
  const [error, setError] = useState(null);
  const [paying, setPaying] = useState(false);

  // keepError: after a failed pay, keep that request's message rather than the stored one.
  const load = useCallback(
    async ({ keepError = false } = {}) => {
      const { status, body } = await getJson(`/checkout-api/sessions/${encodeURIComponent(cs)}`);
      if (status === 404) return setLoadError("missing");
      if (status !== 200) return setLoadError("retry");
      setLoadError(null);
      // A paid session shows a confirmation instead of forwarding: an automatic redirect would
      // turn every paid link into a redirector to whatever https URL its merchant chose.
      setSession(body);
      if (!keepError) setError(body.last_payment_error?.message ?? null);
    },
    [cs],
  );

  useEffect(() => {
    load();
    // Back/forward cache restores a page mid-submit: reset it and refresh the session.
    function onShow(event) {
      if (event.persisted) {
        setPaying(false);
        load();
      }
    }
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, [load]);

  async function pay(event) {
    event.preventDefault();
    setPaying(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const fields = {};
    for (const name of ["number", "exp_month", "exp_year", "cvc"]) {
      fields[`card[${name}]`] = form.get(name);
    }
    const { status, body } = await postForm(
      `/checkout-api/sessions/${encodeURIComponent(cs)}/pay`,
      fields,
    );
    if (status === 200 && body.redirect) return window.location.assign(body.redirect);
    // 402: declined (the session is unchanged). 400/404: already paid, expired or invalid
    // input; reload the session to see which, keeping this message.
    setError(body.error?.message ?? RETRY_MESSAGE);
    setPaying(false);
    if (status === 400 || status === 404) await load({ keepError: true });
  }

  if (loadError === "missing") {
    return (
      <main className="page">
        <p>This checkout link is invalid.</p>
      </main>
    );
  }
  if (loadError === "retry") {
    return (
      <main className="page">
        <p role="alert">Couldn't load checkout.</p>
        <button type="button" onClick={() => load()}>
          Try again
        </button>
      </main>
    );
  }
  if (!session) return <main className="page"><p>Loading…</p></main>;
  if (session.status === "complete") {
    let host = "";
    try {
      host = new URL(session.success_url).host;
    } catch {
      host = "";
    }
    return (
      <main className="page">
        <h1>Payment complete</h1>
        <p>This payment has already been made. Nothing more to do here.</p>
        <a href={session.success_url}>Continue to {host || "the store"}</a>
      </main>
    );
  }
  if (session.status === "expired") {
    return (
      <main className="page">
        <p>This checkout session has expired.</p>
        {session.cancel_url && <a href={session.cancel_url}>Return to the store</a>}
      </main>
    );
  }

  const total = money(session.amount_total, session.currency);
  return (
    <main className="page">
      <h1>Checkout</h1>
      <ul className="items">
        {session.line_items.map((item, i) => (
          <li key={i}>
            <span>
              {item.name} × {item.quantity}
            </span>
            <span>{money(item.amount_total, session.currency)}</span>
          </li>
        ))}
      </ul>
      <p className="total">Total {total}</p>
      <form onSubmit={pay} className="card-form">
        <label>
          Card number
          <input name="number" inputMode="numeric" autoComplete="off" required />
        </label>
        <div className="row">
          <label>
            Expiry month
            <input name="exp_month" inputMode="numeric" placeholder="MM" autoComplete="off" required />
          </label>
          <label>
            Expiry year
            <input name="exp_year" inputMode="numeric" placeholder="YYYY" autoComplete="off" required />
          </label>
          <label>
            CVC
            <input name="cvc" inputMode="numeric" autoComplete="off" required />
          </label>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={paying}>
          Pay {total}
        </button>
      </form>
      {session.cancel_url && (
        <p>
          <a href={session.cancel_url}>Cancel and return to the store</a>
        </p>
      )}
      <details className="test-cards">
        <summary>Test cards</summary>
        <ul>
          {TEST_CARDS.map(([number, what]) => (
            <li key={number}>
              <code>{number}</code> {what}
            </li>
          ))}
        </ul>
        <p>Use any future expiry and any CVC.</p>
      </details>
    </main>
  );
}
