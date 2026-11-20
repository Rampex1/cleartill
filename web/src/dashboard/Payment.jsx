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
  const [captureKey, renewCaptureKey] = useIdempotencyKey();
  const [refundKey, renewRefundKey] = useIdempotencyKey();

  const load = useCallback(async () => {
    const res = await dashGet(`/v1/payment_intents/${encodeURIComponent(id)}`);
    if (res.status !== 200) return setError(errorMessage(res));
    setPi(res.body);
    if (res.body.latest_charge) {
      const c = await dashGet(`/v1/charges/${encodeURIComponent(res.body.latest_charge)}`);
      if (c.status === 200) setCharge(c.body);
    }
    const r = await dashGet(`/v1/refunds?${new URLSearchParams({ payment_intent: id, limit: 100 })}`);
    if (r.status === 200) setRefunds(r.body.data);
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(path, fields, key, renew) {
    setBusy(true);
    setError(null);
    const res = await dashPost(path, fields, key);
    renew(); // the response arrived: the next click is a new action
    if (res.status !== 200) setError(errorMessage(res));
    await load();
    setBusy(false);
  }

  function capture() {
    act(`/v1/payment_intents/${encodeURIComponent(id)}/capture`, {}, captureKey, renewCaptureKey);
  }

  function refund(event) {
    event.preventDefault();
    const amount = new FormData(event.currentTarget).get("amount").trim();
    const fields = { payment_intent: id, ...(amount ? { amount } : {}) };
    act("/v1/refunds", fields, refundKey, renewRefundKey);
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
            Refund amount
            <input name="amount" inputMode="numeric" placeholder="All remaining (cents)" />
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
