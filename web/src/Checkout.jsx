import { AlertCircle, CheckCircle2, ChevronRight, Clock3, Lock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { RETRY_MESSAGE, getJson, postForm } from "./api.js";
import { LogoMark, Money, Spinner, money } from "./ui.jsx";

const TEST_CARDS = [
  ["4242 4242 4242 4242", "Succeeds"],
  ["4000 0000 0000 0002", "Declined"],
  ["4000 0027 6000 3184", "Asks for 3D Secure"],
];

function cardBrand(digits) {
  if (/^4/.test(digits)) return "VISA";
  if (/^(5[1-5]|2[2-7])/.test(digits)) return "MC";
  if (/^3[47]/.test(digits)) return "AMEX";
  return null;
}

function groupCardNumber(value) {
  const digits = value.replace(/\D/g, "").slice(0, 19);
  return digits.replace(/(\d{4})(?=\d)/g, "$1 ");
}

function merchantHost(session) {
  try {
    return new URL(session.success_url).host;
  } catch {
    return "";
  }
}

function Merchant({ host }) {
  return (
    <div className="merchant">
      <span className="merchant-mark" aria-hidden="true">
        {(host || "?").replace(/^www\./, "").charAt(0)}
      </span>
      {host || "Merchant"}
    </div>
  );
}

function PoweredBy() {
  return (
    <p className="checkout-foot">
      <span className="row powered">
        Powered by <LogoMark size={14} /> Cleartill
      </span>
    </p>
  );
}

export default function Checkout() {
  const { cs } = useParams();
  const [session, setSession] = useState(null);
  const [loadError, setLoadError] = useState(null); // "missing" | "retry"
  const [error, setError] = useState(null);
  const [paying, setPaying] = useState(false);
  const [number, setNumber] = useState("");

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
      fields[`card[${name}]`] = String(form.get(name) ?? "").trim();
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
      <main className="center-page">
        <div className="center-card">
          <div className="status-icon neutral">
            <AlertCircle size={24} aria-hidden="true" />
          </div>
          <h1>Link not found</h1>
          <p>This checkout link is invalid.</p>
        </div>
      </main>
    );
  }
  if (loadError === "retry") {
    return (
      <main className="center-page">
        <div className="center-card">
          <h1>Checkout unavailable</h1>
          <p role="alert">Couldn&apos;t load checkout.</p>
          <button type="button" className="btn btn-primary" onClick={() => load()}>
            Try again
          </button>
        </div>
      </main>
    );
  }
  if (!session) {
    return (
      <main className="checkout" aria-busy="true">
        <div className="checkout-summary" />
        <div className="checkout-pay" />
      </main>
    );
  }

  const host = merchantHost(session);
  if (session.status === "complete") {
    return (
      <main className="center-page">
        <div className="center-card">
          <div className="status-icon">
            <CheckCircle2 size={24} aria-hidden="true" />
          </div>
          <h1>Payment complete</h1>
          <p>
            This payment of {money(session.amount_total, session.currency)} has already been made.
            Nothing more to do here.
          </p>
          <p className="hero-actions">
            <a className="btn btn-primary" href={session.success_url}>
              Continue to {host || "the store"}
            </a>
          </p>
        </div>
      </main>
    );
  }
  if (session.status === "expired") {
    return (
      <main className="center-page">
        <div className="center-card">
          <div className="status-icon neutral">
            <Clock3 size={24} aria-hidden="true" />
          </div>
          <h1>Checkout expired</h1>
          <p>This checkout session has expired. Start again from the store.</p>
          {session.cancel_url && (
            <p className="hero-actions">
              <a className="btn btn-secondary" href={session.cancel_url}>
                Return to the store
              </a>
            </p>
          )}
        </div>
      </main>
    );
  }

  const total = money(session.amount_total, session.currency);
  const brand = cardBrand(number.replace(/\D/g, ""));
  return (
    <main className="checkout">
      <section className="checkout-summary" aria-label="Order summary">
        <div className="checkout-summary-inner">
          <Merchant host={host} />
          <p className="checkout-due">Amount due</p>
          <div className="checkout-total">
            <Money amount={session.amount_total} currency={session.currency} display />
          </div>
          <ul className="line-items">
            {session.line_items.map((item, i) => (
              <li key={i}>
                <span>
                  <span className="line-item-name">{item.name}</span>
                  <span className="line-item-qty">
                    Qty {item.quantity} × {money(item.unit_amount, session.currency)}
                  </span>
                </span>
                <Money amount={item.amount_total} currency={session.currency} />
              </li>
            ))}
          </ul>
          <div className="checkout-totals">
            <div>
              <span>Subtotal</span>
              <Money amount={session.amount_total} currency={session.currency} />
            </div>
            <div>
              <span>Total due</span>
              <Money amount={session.amount_total} currency={session.currency} />
            </div>
          </div>
        </div>
      </section>

      <section className="checkout-pay" aria-label="Payment">
        <div className="checkout-pay-inner">
          <h1>Pay with card</h1>
          <form onSubmit={pay} className="card-fields" noValidate>
            <label className="field card-number">
              Card number
              <input
                className="input"
                name="number"
                inputMode="numeric"
                autoComplete="off"
                placeholder="1234 1234 1234 1234"
                value={number}
                onChange={(e) => setNumber(groupCardNumber(e.target.value))}
                aria-invalid={Boolean(error) || undefined}
                required
              />
              {brand && <span className="brand-tag">{brand}</span>}
            </label>
            <div className="split">
              <label className="field">
                Expiry month
                <input
                  className="input"
                  name="exp_month"
                  inputMode="numeric"
                  placeholder="MM"
                  maxLength={2}
                  autoComplete="off"
                  required
                />
              </label>
              <label className="field">
                Expiry year
                <input
                  className="input"
                  name="exp_year"
                  inputMode="numeric"
                  placeholder="YYYY"
                  maxLength={4}
                  autoComplete="off"
                  required
                />
              </label>
              <label className="field">
                CVC
                <input
                  className="input"
                  name="cvc"
                  inputMode="numeric"
                  placeholder="123"
                  maxLength={4}
                  autoComplete="off"
                  required
                />
              </label>
            </div>
            {error && (
              <p className="alert" role="alert">
                <AlertCircle size={16} aria-hidden="true" />
                {error}
              </p>
            )}
            <button type="submit" className="btn btn-primary btn-lg btn-block pay-button" disabled={paying}>
              {paying ? <Spinner /> : <Lock size={15} aria-hidden="true" />}
              Pay {total}
            </button>
          </form>
          <p className="secure-note">
            <Lock size={13} aria-hidden="true" />
            Card details go only to Cleartill and are never stored.
          </p>
          {session.cancel_url && (
            <p className="checkout-foot">
              <a href={session.cancel_url}>Cancel and return to {host || "the store"}</a>
            </p>
          )}
          <details className="test-cards">
            <summary>
              <ChevronRight size={15} className="chev" aria-hidden="true" />
              Test cards
            </summary>
            <ul>
              {TEST_CARDS.map(([n, what]) => (
                <li key={n}>
                  <code>{n}</code>
                  <span>{what}</span>
                </li>
              ))}
            </ul>
            <p>Any future expiry date and any CVC.</p>
          </details>
          <PoweredBy />
        </div>
      </section>
    </main>
  );
}
