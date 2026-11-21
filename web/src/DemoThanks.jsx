import { CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getJson } from "./api.js";
import { useDemoAccount } from "./dashboard/useDemoAccount.js";
import { useDemoCheckout } from "./Home.jsx";
import { Timeline } from "./Timeline.jsx";
import { Money } from "./ui.jsx";

export default function DemoThanks() {
  const [params] = useSearchParams();
  const cs = params.get("session");
  const [session, setSession] = useState(null);
  const [payment, setPayment] = useState(null);
  const demo = useDemoCheckout();
  const account = useDemoAccount();

  useEffect(() => {
    if (!cs) return;
    const id = encodeURIComponent(cs);
    getJson(`/checkout-api/sessions/${id}`).then(({ status, body }) => {
      if (status === 200) setSession(body);
    });
    getJson(`/checkout-api/demo/sessions/${id}/payment`).then(({ status, body }) => {
      if (status === 200) setPayment(body);
    });
  }, [cs]);

  return (
    <main className="center-page">
      <div className="center-card receipt-card">
        <div className="status-icon">
          <CheckCircle2 size={24} aria-hidden="true" />
        </div>
        <h1>Thanks, your demo payment went through</h1>
        <p>
          The simulated network approved it, it was captured, and the ledger booked the funds as
          pending for the demo store. Here is what happened, step by step.
        </p>
        {session && (
          <div className="bank-frame">
            <dl className="facts">
              <dt>Amount</dt>
              <dd>
                <Money amount={session.amount_total} currency={session.currency} />
              </dd>
              <dt>Items</dt>
              <dd>{session.line_items.map((i) => i.name).join(", ")}</dd>
              <dt>Session</dt>
              <dd className="id">{session.id}</dd>
            </dl>
          </div>
        )}
        {payment && (
          <section className="receipt-timeline" aria-label="What happened">
            <Timeline timeline={payment} />
          </section>
        )}
        <p className="receipt-next">
          This payment belongs to the built-in demo store. To see payments in a dashboard, open a
          demo account (already full of history) or create your own.
        </p>
        <div className="button-pair">
          <button
            type="button"
            className="btn btn-primary"
            onClick={account.start}
            disabled={account.busy}
          >
            Explore a demo account
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={demo.start}
            disabled={demo.busy}
          >
            Pay another
          </button>
        </div>
        <p className="receipt-foot">
          <Link to="/dashboard/signup">Or create your own account</Link>
        </p>
      </div>
    </main>
  );
}
