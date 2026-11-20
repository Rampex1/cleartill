import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { getJson, money, postForm } from "./api.js";

const TEST_CARDS = [
  ["4242 4242 4242 4242", "succeeds"],
  ["4000 0000 0000 0002", "is declined"],
  ["4000 0027 6000 3184", "asks for 3D Secure"],
];

export default function Checkout() {
  const { cs } = useParams();
  const [session, setSession] = useState(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState(null);
  const [paying, setPaying] = useState(false);

  const load = useCallback(async () => {
    const { status, body } = await getJson(`/checkout-api/sessions/${encodeURIComponent(cs)}`);
    if (status !== 200) return setMissing(true);
    if (body.status === "complete") return window.location.replace(body.success_url);
    setSession(body);
    if (body.last_payment_error) setError(body.last_payment_error.message);
  }, [cs]);

  useEffect(() => {
    load();
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
    // 402: declined. 400/404: already paid, expired, or invalid input; reload to see which.
    setError(body.error?.message ?? "Something went wrong. Please try again.");
    setPaying(false);
    if (status !== 402) await load();
  }

  if (missing) return <main className="page"><p>This checkout link is invalid.</p></main>;
  if (!session) return <main className="page"><p>Loading…</p></main>;
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
            <input name="exp_month" inputMode="numeric" placeholder="MM" required />
          </label>
          <label>
            Expiry year
            <input name="exp_year" inputMode="numeric" placeholder="YYYY" required />
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
