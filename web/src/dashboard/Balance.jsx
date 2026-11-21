import { useCallback, useEffect, useState } from "react";

import { money } from "../api.js";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";
import { date } from "./Payments.jsx";

function total(list) {
  return list.map((b) => money(b.amount, b.currency)).join(", ") || money(0, "usd");
}

export default function Balance() {
  const [balance, setBalance] = useState(null);
  const [txns, setTxns] = useState(null);
  const [payouts, setPayouts] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [keyFor, settle] = useIdempotencyKey();

  // `isStale` lets a load from an unmounted page drop its result.
  const load = useCallback(async (isStale = () => false) => {
    // ponytail: newest 100 transactions and payouts; add paging when a merchant has more.
    const [b, t, p] = await Promise.all([
      dashGet("/v1/balance"),
      dashGet("/v1/balance_transactions?limit=100"),
      dashGet("/v1/payouts?limit=100"),
    ]);
    if (isStale()) return;
    for (const res of [b, t, p]) if (res.status !== 200) setError(errorMessage(res));
    if (b.status === 200) setBalance(b.body);
    if (t.status === 200) setTxns(t.body.data);
    if (p.status === 200) setPayouts(p.body.data);
  }, []);

  useEffect(() => {
    let stale = false;
    load(() => stale);
    return () => {
      stale = true;
    };
  }, [load]);

  async function payOut(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = { amount: new FormData(form).get("amount").trim(), currency: "usd" };
    setBusy(true);
    setError(null);
    const res = await dashPost("/v1/payouts", fields, keyFor(fields));
    settle(res.status);
    if (res.status === 200) form.reset();
    else setError(errorMessage(res));
    await load();
    setBusy(false);
  }

  return (
    <>
      <h1>Balance</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {balance && (
        <dl className="facts">
          <dt>Available</dt>
          <dd>{total(balance.available)}</dd>
          <dt>Pending</dt>
          <dd>{total(balance.pending)}</dd>
        </dl>
      )}
      <form onSubmit={payOut} className="row refund">
        <label>
          Payout amount (cents)
          <input name="amount" inputMode="numeric" required />
        </label>
        <button type="submit" disabled={busy}>
          Pay out
        </button>
      </form>
      <h2>Payouts</h2>
      {payouts && payouts.length === 0 && <p>No payouts yet.</p>}
      {payouts && payouts.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Amount</th>
              <th>Status</th>
              <th>Arrives</th>
            </tr>
          </thead>
          <tbody>
            {payouts.map((p) => (
              <tr key={p.id}>
                <td>{money(p.amount, p.currency)}</td>
                <td>{p.status}</td>
                <td>{date(p.arrival_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Balance transactions</h2>
      {txns && txns.length === 0 && <p>No balance transactions yet.</p>}
      {txns && txns.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Amount</th>
              <th>Fee</th>
              <th>Net</th>
              <th>Status</th>
              <th>Available on</th>
            </tr>
          </thead>
          <tbody>
            {txns.map((t) => (
              <tr key={t.id}>
                <td>{t.type}</td>
                <td>{money(t.amount, t.currency)}</td>
                <td>{money(t.fee, t.currency)}</td>
                <td>{money(t.net, t.currency)}</td>
                <td>{t.status}</td>
                <td>{date(t.available_on)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
