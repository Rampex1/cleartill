import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { money } from "../api.js";
import { dashGet, errorMessage } from "./dash.js";

const PAGE_SIZE = 20;

export function date(unix) {
  return new Date(unix * 1000).toLocaleString();
}

export default function Payments() {
  const [cursor, setCursor] = useState(null);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false; // a slower, older page must not overwrite a newer one
    const query = new URLSearchParams({ limit: PAGE_SIZE });
    if (cursor) query.set("starting_after", cursor);
    setError(null);
    dashGet(`/v1/payment_intents?${query}`).then((res) => {
      if (stale) return;
      if (res.status === 200) setPage(res.body);
      else setError(errorMessage(res));
    });
    return () => {
      stale = true;
    };
  }, [cursor]);

  return (
    <>
      <h1>Payments</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {page && page.data.length === 0 && <p>No payments yet.</p>}
      {page && page.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Payment</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {page.data.map((pi) => (
              <tr key={pi.id}>
                <td>
                  <Link to={`/dashboard/payments/${pi.id}`}>{pi.id}</Link>
                </td>
                <td>{money(pi.amount, pi.currency)}</td>
                <td>{pi.status}</td>
                <td>{date(pi.created)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        {cursor && (
          <button type="button" className="secondary" onClick={() => setCursor(null)}>
            First page
          </button>
        )}
        {page?.has_more && (
          <button type="button" onClick={() => setCursor(page.data.at(-1).id)}>
            Next
          </button>
        )}
      </div>
    </>
  );
}
