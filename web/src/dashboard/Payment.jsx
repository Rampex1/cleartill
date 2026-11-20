import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { money } from "../api.js";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";
import { date } from "./Payments.jsx";

export default function Payment() {
  const { id } = useParams();
  const [pi, setPi] = useState(null);
  const [charge, setCharge] = useState(null);
  const [refunds, setRefunds] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
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
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(path, fields, keyFor, settle) {
    setBusy(true);
    setError(null);
    const res = await dashPost(path, fields, keyFor(fields));
    settle(res.status); // keeps the key only when no response arrived (safe retry)
    if (res.status !== 200) setError(errorMessage(res));
    await load();
    setBusy(false);
    return res.status === 200;
  }

  function capture() {
    act(`/v1/payment_intents/${encodeURIComponent(id)}/capture`, {}, captureKeyFor, settleCapture);
  }

  async function refund(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const amount = new FormData(form).get("amount").trim();
    const fields = { payment_intent: id, ...(amount ? { amount } : {}) };
    if (await act("/v1/refunds", fields, refundKeyFor, settleRefund)) form.reset();
  }

  if (!pi) {
    return error ? (
      <p className="error" role="alert">
        {error}
      </p>
    ) : (
      <p>Loading…</p>
    );
  }

  const refundable =
    charge?.captured && charge.amount_refunded < charge.amount_captured && pi.status === "succeeded";
  return (
    <>
      <p>
        <Link to="/dashboard/payments">← Payments</Link>
      </p>
      <h1>Payment</h1>
      <dl className="facts">
        <dt>ID</dt>
        <dd>
          <code>{pi.id}</code>
        </dd>
        <dt>Amount</dt>
        <dd>{money(pi.amount, pi.currency)}</dd>
        <dt>Status</dt>
        <dd>{pi.status}</dd>
        <dt>Capture method</dt>
        <dd>{pi.capture_method}</dd>
        <dt>Created</dt>
        <dd>{date(pi.created)}</dd>
        {charge && (
          <>
            <dt>Charge</dt>
            <dd>
              <code>{charge.id}</code> {charge.status}
              {charge.captured ? `, captured ${money(charge.amount_captured, pi.currency)}` : ""}
            </dd>
          </>
        )}
      </dl>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pi.status === "requires_capture" && (
        <button type="button" disabled={busy} onClick={capture}>
          Capture
        </button>
      )}
      {refundable && (
        <form onSubmit={refund} className="row refund">
          <label>
            Refund amount (cents)
            <input name="amount" inputMode="numeric" placeholder="Blank refunds all remaining" />
          </label>
          <button type="submit" disabled={busy}>
            Refund
          </button>
        </form>
      )}
      {refunds.length > 0 && (
        <>
          <h2>Refunds</h2>
          <ul>
            {refunds.map((r) => (
              <li key={r.id}>Refunded {money(r.amount, r.currency)}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
