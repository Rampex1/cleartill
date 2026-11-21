import { AlertCircle, ArrowUpRight, Banknote, Landmark } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, Money, Spinner, StateBadge, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";

function total(list) {
  return list.reduce((sum, b) => sum + (b.currency === "usd" ? b.amount : 0), 0);
}

const TXN_TYPE = { charge: "Charge", refund: "Refund", payout: "Payout" };

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

  const inTransit = (payouts ?? [])
    .filter((p) => p.status === "pending")
    .reduce((sum, p) => sum + p.amount, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Balance</h1>
          <p>Funds settle into pending, become available, then pay out to your bank.</p>
        </div>
      </div>

      <section className="panel">
        <dl className="summary">
          <div>
            <dt>Available</dt>
            <dd className="money-display-wrap">
              {balance ? <Money amount={total(balance.available)} display /> : <span className="skeleton" />}
            </dd>
            <dd className="summary-note">
              {balance && total(balance.available) < 0
                ? "Refunds exceed available funds; new captures cover it"
                : "Ready to pay out"}
            </dd>
          </div>
          <div>
            <dt>Pending</dt>
            <dd className="money-display-wrap">
              {balance ? <Money amount={total(balance.pending)} display /> : <span className="skeleton" />}
            </dd>
            <dd className="summary-note">Captured, not yet released</dd>
          </div>
          <div>
            <dt>In transit</dt>
            <dd className="money-display-wrap">
              {payouts ? <Money amount={inTransit} display /> : <span className="skeleton" />}
            </dd>
            <dd className="summary-note">Payouts on their way to your bank</dd>
          </div>
        </dl>
        <form onSubmit={payOut} className="payout-form">
          <label className="field">
            Payout amount (cents)
            <input className="input" name="amount" inputMode="numeric" placeholder="e.g. 500" required />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? <Spinner /> : <Landmark size={15} aria-hidden="true" />}
            Pay out
          </button>
          {error && (
            <p className="alert payout-error" role="alert">
              <AlertCircle size={16} aria-hidden="true" />
              {error}
            </p>
          )}
        </form>
      </section>

      <div className="stack balance-tables">
        <section className="panel">
          <div className="panel-head">
            <h2>Payouts</h2>
            <p>Sent to the simulated bank</p>
          </div>
          {payouts && payouts.length === 0 ? (
            <EmptyState icon={Landmark} title="No payouts yet">
              Once funds are available, pay them out above. They arrive after the availability
              delay.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table table-labelled">
                <thead>
                  <tr>
                    <th className="num">Amount</th>
                    <th>Status</th>
                    <th>Arrives</th>
                    <th>Payout</th>
                  </tr>
                </thead>
                <tbody>
                  {(payouts ?? []).map((p) => (
                    <tr key={p.id}>
                      <td className="num strong" data-label="Amount">
                        <Money amount={p.amount} currency={p.currency} />
                      </td>
                      <td data-label="Status">
                        <StateBadge state={p.status} />
                      </td>
                      <td className="muted" data-label="Arrives">{when(p.arrival_date)}</td>
                      <td className="id" data-label="Payout">{p.id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Balance transactions</h2>
            <p>Every movement, with fees and net</p>
          </div>
          {txns && txns.length === 0 ? (
            <EmptyState icon={Banknote} title="Nothing has moved yet">
              Captures, refunds and payouts each create a balance transaction here.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table table-labelled">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th className="num">Amount</th>
                    <th className="num">Fee</th>
                    <th className="num">Net</th>
                    <th>Status</th>
                    <th>Available on</th>
                  </tr>
                </thead>
                <tbody>
                  {(txns ?? []).map((t) => (
                    <tr key={t.id}>
                      <td className="strong" data-label="Type">
                        <span className="row">
                          <ArrowUpRight
                            size={14}
                            aria-hidden="true"
                            className={t.amount < 0 ? "flow-out" : "flow-in"}
                          />
                          <span className="visually-hidden">{t.type}</span>
                          <span aria-hidden="true">{TXN_TYPE[t.type] ?? t.type}</span>
                        </span>
                      </td>
                      <td className="num" data-label="Amount">
                        <Money amount={t.amount} currency={t.currency} />
                      </td>
                      <td className="num muted" data-label="Fee">
                        <Money amount={t.fee} currency={t.currency} />
                      </td>
                      <td className="num strong" data-label="Net">
                        <Money amount={t.net} currency={t.currency} />
                      </td>
                      <td data-label="Status">
                        <StateBadge state={t.status} />
                      </td>
                      <td className="muted" data-label="Available on">
                        {when(t.available_on)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
