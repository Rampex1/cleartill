import { useEffect, useState } from "react";

import { money } from "../api.js";
import { dashGet, errorMessage } from "./dash.js";
import { date } from "./Payments.jsx";

function total(list) {
  return list.map((b) => money(b.amount, b.currency)).join(", ") || money(0, "usd");
}

export default function Balance() {
  const [balance, setBalance] = useState(null);
  const [txns, setTxns] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    dashGet("/v1/balance").then((res) =>
      res.status === 200 ? setBalance(res.body) : setError(errorMessage(res)),
    );
    // ponytail: newest 100 balance transactions; add paging when a merchant has more.
    dashGet("/v1/balance_transactions?limit=100").then((res) =>
      res.status === 200 ? setTxns(res.body.data) : setError(errorMessage(res)),
    );
  }, []);

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
