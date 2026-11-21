import { ChevronLeft, ChevronRight, Users } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState, SkeletonRows, when } from "../ui.jsx";
import { dashGet, errorMessage } from "./dash.js";

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
      <div className="page-head">
        <div>
          <h1>Customers</h1>
          <p>People and businesses you&apos;ve charged or saved.</p>
        </div>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="panel">
        {page && page.data.length === 0 ? (
          <EmptyState icon={Users} title="No customers yet">
            Create one with <code>POST /v1/customers</code> and attach it to payments to see their
            history in one place.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Customer</th>
                  <th>Created</th>
                </tr>
              </thead>
              {page ? (
                <tbody>
                  {page.data.map((c) => (
                    <tr key={c.id}>
                      <td className="strong">{c.name ?? "—"}</td>
                      <td>{c.email ?? "—"}</td>
                      <td className="id">{c.id}</td>
                      <td className="muted">{when(c.created)}</td>
                    </tr>
                  ))}
                </tbody>
              ) : (
                <SkeletonRows columns={4} />
              )}
            </table>
          </div>
        )}
        {page && (cursor || page.has_more) && (
          <div className="table-foot">
            <span>{page.data.length} shown</span>
            <span className="row">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!cursor}
                onClick={() => setCursor(null)}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                First page
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!page.has_more}
                onClick={() => setCursor(page.data.at(-1).id)}
              >
                Next
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </span>
          </div>
        )}
      </div>
    </>
  );
}
