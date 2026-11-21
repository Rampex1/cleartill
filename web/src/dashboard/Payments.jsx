import { ChevronLeft, ChevronRight, CreditCard } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, Money, PaymentStatus, SkeletonRows, when } from "../ui.jsx";
import { dashGet, errorMessage } from "./dash.js";

const PAGE_SIZE = 20;

export default function Payments() {
  const navigate = useNavigate();
  const [cursor, setCursor] = useState(null);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false; // a slower, older page must not overwrite a newer one
    const query = new URLSearchParams({ limit: PAGE_SIZE, "expand[]": "data.customer" });
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
      <div className="page-head">
        <div>
          <h1>Payments</h1>
          <p>Every payment intent on this account, newest first.</p>
        </div>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="panel">
        {page && page.data.length === 0 ? (
          <EmptyState icon={CreditCard} title="No payments yet">
            Create a payment intent with your secret key, or pay a checkout session, and it
            appears here with its full timeline.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table table-stack">
              <thead>
                <tr>
                  <th className="num">Amount</th>
                  <th>Status</th>
                  <th>Payment</th>
                  <th>Customer</th>
                  <th>Date</th>
                  <th>
                    <span className="visually-hidden">Open</span>
                  </th>
                </tr>
              </thead>
              {page ? (
                <tbody>
                  {page.data.map((pi) => (
                    <tr
                      key={pi.id}
                      className="row-clickable"
                      // The whole row opens the payment; the id link stays the keyboard target.
                      onClick={(e) => {
                        if (!e.target.closest("a, button")) navigate(`/dashboard/payments/${pi.id}`);
                      }}
                    >
                      <td className="num strong">
                        <Money amount={pi.amount} currency={pi.currency} />
                      </td>
                      <td>
                        <PaymentStatus
                          status={pi.status}
                          failed={Boolean(pi.last_payment_error)}
                        />
                      </td>
                      <td>
                        <Link className="id-link" to={`/dashboard/payments/${pi.id}`}>
                          {pi.id}
                        </Link>
                      </td>
                      <td>
                        {pi.customer ? (
                          <span className="person">
                            <strong>{pi.customer.name ?? pi.customer.email ?? pi.customer.id}</strong>
                            {pi.customer.name && pi.customer.email && <small>{pi.customer.email}</small>}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="muted">{when(pi.created)}</td>
                      <td className="num muted row-chevron">
                        <ChevronRight size={16} aria-hidden="true" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              ) : (
                <SkeletonRows columns={6} />
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
