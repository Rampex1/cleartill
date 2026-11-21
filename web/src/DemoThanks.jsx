import { CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getJson } from "./api.js";
import { useDemoCheckout } from "./Home.jsx";
import { Money } from "./ui.jsx";

export default function DemoThanks() {
  const [params] = useSearchParams();
  const cs = params.get("session");
  const [session, setSession] = useState(null);
  const demo = useDemoCheckout();

  useEffect(() => {
    if (!cs) return;
    getJson(`/checkout-api/sessions/${encodeURIComponent(cs)}`).then(({ status, body }) => {
      if (status === 200) setSession(body);
    });
  }, [cs]);

  return (
    <main className="center-page">
      <div className="center-card">
        <div className="status-icon">
          <CheckCircle2 size={24} aria-hidden="true" />
        </div>
        <h1>Thanks, your demo payment went through</h1>
        <p>
          The simulated network approved it, the payment was captured, and the ledger booked the
          funds as pending for the demo store.
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
        <div className="button-pair">
          <Link className="btn btn-primary" to="/dashboard/signup">
            Create an account
          </Link>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={demo.start}
            disabled={demo.busy}
          >
            Pay another
          </button>
        </div>
      </div>
    </main>
  );
}
