import { useEffect, useState } from "react";

import { dashGet, errorMessage } from "./dash.js";
import { date } from "./Payments.jsx";

export default function Customers() {
  const [cursor, setCursor] = useState(null);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false;
    const query = new URLSearchParams({ limit: 20 });
    if (cursor) query.set("starting_after", cursor);
    setError(null);
    dashGet(`/v1/customers?${query}`).then((res) => {
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
      <h1>Customers</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {page && page.data.length === 0 && <p>No customers yet.</p>}
      {page && page.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Email</th>
              <th>Name</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {page.data.map((c) => (
              <tr key={c.id}>
                <td>
                  <code>{c.id}</code>
                </td>
                <td>{c.email ?? "—"}</td>
                <td>{c.name ?? "—"}</td>
                <td>{date(c.created)}</td>
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
