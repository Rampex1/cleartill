import { ArrowLeft, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Timeline } from "../Timeline.jsx";
import { Money, PaymentStatus, Spinner, fullTime, money, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";

export default function Payment() {
  const { id } = useParams();
  const [pi, setPi] = useState(null);
  const [charge, setCharge] = useState(null);
  const [refunds, setRefunds] = useState([]);
  const [timeline, setTimeline] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // "capture" | "refund" | null
  const [captureKeyFor, settleCapture] = useIdempotencyKey();
  const [refundKeyFor, settleRefund] = useIdempotencyKey();

  const load = useCallback(async () => {
    const res = await dashGet(`/v1/payment_intents/${encodeURIComponent(id)}`);
    if (res.status !== 200) return setError(errorMessage(res));
    setPi(res.body);
    let latest = null;
    if (res.body.latest_charge) {
      const c = await dashGet(`/v1/charges/${encodeURIComponent(res.body.latest_charge)}`);
      latest = c.status === 200 ? c.body : null; // never act on a stale charge
    }
    setCharge(latest);
    // ponytail: first 100 refunds only; paginate if a payment ever has more.
    const r = await dashGet(`/v1/refunds?${new URLSearchParams({ payment_intent: id, limit: 100 })}`);
    if (r.status === 200) setRefunds(r.body.data);
    const t = await dashGet(`/dashboard-api/payment_intents/${encodeURIComponent(id)}/timeline`);
    if (t.status === 200) setTimeline(t.body);
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(kind, path, fields, keyFor, settle, onSuccess = () => {}) {
    setBusy(kind);
    setError(null);
    const res = await dashPost(path, fields, keyFor(fields));
    settle(res.status); // keeps the key only when no response arrived (safe retry)
    // Clear inputs the moment it succeeded, not after the reload: anything typed meanwhile
    // is the next action and must survive.
    if (res.status === 200) onSuccess();
    else setError(errorMessage(res));
    await load();
    setBusy(null);
  }

  function capture() {
    act(
      "capture",
      `/v1/payment_intents/${encodeURIComponent(id)}/capture`,
      {},
      captureKeyFor,
      settleCapture,
    );
  }

  async function refund(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const amount = new FormData(form).get("amount").trim();
    const fields = { payment_intent: id, ...(amount ? { amount } : {}) };
    await act("refund", "/v1/refunds", fields, refundKeyFor, settleRefund, () => form.reset());
  }

  if (!pi) {
    return error ? (
      <p className="alert" role="alert">
        {error}
      </p>
    ) : (
      <div className="stack-sm" aria-hidden="true">
        <span className="skeleton loading-bar" />
      </div>
    );
  }

  const refundable =
    charge?.captured && charge.amount_refunded < charge.amount_captured && pi.status === "succeeded";
  const remaining = charge ? charge.amount_captured - charge.amount_refunded : 0;
  const fullyRefunded = charge?.captured && charge.amount_refunded === charge.amount_captured;
  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/dashboard/payments">
          <ArrowLeft size={14} aria-hidden="true" />
          All payments
        </Link>
        <span aria-hidden="true">/</span>
        <h1>Payment</h1>
      </nav>
      <div className="payment-head">
        <div className="payment-title">
          <div className="payment-amount">
            <Money amount={pi.amount} currency={pi.currency} display showCurrency />
            <PaymentStatus
              status={pi.status}
              refunded={fullyRefunded}
              failed={Boolean(pi.last_payment_error)}
            />
          </div>
          <span className="id">{pi.id}</span>
        </div>
      </div>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      <div className="payment-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>Timeline</h2>
            <p>Events and the ledger postings behind them</p>
          </div>
          <div className="panel-body">
            <Timeline timeline={timeline} pi={pi} />
          </div>
        </section>

        <div className="stack">
          {(pi.status === "requires_capture" || refundable) && (
            <section className="panel">
              <div className="panel-head">
                <h2>{pi.status === "requires_capture" ? "Capture funds" : "Issue a refund"}</h2>
              </div>
              <div className="panel-body action-form">
                {pi.status === "requires_capture" && (
                  <>
                    <p className="muted-text">
                      {money(pi.amount_capturable, pi.currency)} is authorized and waiting. Holds
                      expire after 7 days.
                    </p>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={busy !== null}
                      onClick={capture}
                    >
                      {busy === "capture" && <Spinner />}
                      Capture
                    </button>
                  </>
                )}
                {refundable && (
                  <form onSubmit={refund} className="refund action-form">
                    <label className="field">
                      Refund amount (cents)
                      <input
                        className="input"
                        name="amount"
                        inputMode="numeric"
                        placeholder={`Blank refunds all ${money(remaining, pi.currency)}`}
                      />
                    </label>
                    <button type="submit" className="btn btn-secondary" disabled={busy !== null}>
                      {busy === "refund" ? <Spinner /> : <RotateCcw size={14} aria-hidden="true" />}
                      Refund
                    </button>
                  </form>
                )}
              </div>
            </section>
          )}

          <section className="panel">
            <div className="panel-head">
              <h2>Details</h2>
            </div>
            <div className="panel-body">
              <dl className="facts">
                <dt>Status</dt>
                <dd>
                  <code>{pi.status}</code>
                </dd>
                <dt>Amount received</dt>
                <dd>
                  <Money amount={pi.amount_received} currency={pi.currency} />
                </dd>
                <dt>Capture method</dt>
                <dd>{pi.capture_method === "manual" ? "Manual" : "Automatic"}</dd>
                <dt>Payment method</dt>
                <dd className="id truncate" title={pi.payment_method ?? undefined}>
                  {pi.payment_method ?? "—"}
                </dd>
                <dt>Customer</dt>
                <dd className="id truncate" title={pi.customer ?? undefined}>
                  {pi.customer ?? "—"}
                </dd>
                <dt>Charge</dt>
                <dd>
                  {charge ? (
                    <>
                      <span className="id truncate" title={charge.id}>
                        {charge.id}
                      </span>
                      {charge.captured
                        ? `${money(charge.amount_captured, pi.currency)} captured`
                        : charge.status === "succeeded"
                          ? "Authorized, not captured yet"
                          : "Failed"}
                    </>
                  ) : (
                    "—"
                  )}
                </dd>
                <dt>Created</dt>
                <dd title={fullTime(pi.created)}>{when(pi.created)}</dd>
              </dl>
            </div>
          </section>

          {refunds.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2>Refunds</h2>
                <Money amount={charge?.amount_refunded ?? 0} currency={pi.currency} />
              </div>
              <ul className="refund-list">
                {refunds.map((r) => (
                  <li key={r.id}>
                    <RotateCcw size={14} aria-hidden="true" />
                    <span>
                      Refunded <Money amount={r.amount} currency={r.currency} />
                    </span>
                    <time>{when(r.created)}</time>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
