import { ChevronLeft, ChevronRight, CreditCard, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, Money, PaymentStatus, SkeletonRows, Spinner, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";

const TEST_ORDER = {
  mode: "payment",
  "line_items[0][price_data][currency]": "usd",
  "line_items[0][price_data][unit_amount]": "2400",
  "line_items[0][price_data][product_data][name]": "Field notebook set",
  "line_items[0][quantity]": "1",
  "line_items[1][price_data][currency]": "usd",
  "line_items[1][price_data][unit_amount]": "1500",
  "line_items[1][price_data][product_data][name]": "Linen tote",
  "line_items[1][quantity]": "1",
};

/* A checkout session for this account, opened in the real hosted checkout; paying it lands
   back here with the new payment at the top. */
function useTestPayment() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [keyFor, settle] = useIdempotencyKey();
  async function start() {
    const back = `${window.location.origin}/dashboard/payments`;
    const fields = { ...TEST_ORDER, success_url: back, cancel_url: back };
    setBusy(true);
    setError(null);
    const res = await dashPost("/v1/checkout/sessions", fields, keyFor(fields));
    settle(res.status);
    if (res.status === 200) return window.location.assign(res.body.url);
    setError(errorMessage(res));
    setBusy(false);
  }
  return { start, busy, error };
}

function TestPaymentButton({ test, primary = false }) {
  return (
    <button
      type="button"
      className={`btn ${primary ? "btn-primary" : "btn-secondary"}`}
      onClick={test.start}
      disabled={test.busy}
    >
      {test.busy ? <Spinner /> : <Plus size={15} aria-hidden="true" />}
      Create test payment
    </button>
  );
}

const PAGE_SIZE = 20;

export default function Payments() {
  const navigate = useNavigate();
  const [cursor, setCursor] = useState(null);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);
  const test = useTestPayment();

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
        <div className="page-actions">
          <TestPaymentButton test={test} primary />
        </div>
      </div>
      {(error || test.error) && (
        <p className="alert" role="alert">
          {error ?? test.error}
        </p>
      )}
      <div className="panel">
        {page && page.data.length === 0 ? (
          <EmptyState
            icon={CreditCard}
            title="No payments yet"
            action={<TestPaymentButton test={test} />}
          >
            Make a test payment through the hosted checkout (card 4242 4242 4242 4242), or create
            one with your secret key. It appears here with its full timeline.
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
