import { AlertCircle, CheckCircle2, ChevronRight, Plus, Webhook } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, SkeletonRows, Spinner, StateBadge, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";

export default function Webhooks() {
  const [endpoints, setEndpoints] = useState(null);
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [keyFor, settle] = useIdempotencyKey();

  const load = useCallback(async (isStale = () => false) => {
    // ponytail: first 100 endpoints; page with has_more if a merchant ever has more.
    const res = await dashGet("/v1/webhook_endpoints?limit=100");
    if (isStale()) return;
    if (res.status === 200) setEndpoints(res.body.data);
    else setError(errorMessage(res));
  }, []);

  useEffect(() => {
    let stale = false;
    load(() => stale);
    return () => {
      stale = true;
    };
  }, [load]);

  async function add(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = { url: new FormData(form).get("url").trim(), "enabled_events[]": "*" };
    setBusy(true);
    setError(null);
    const res = await dashPost("/v1/webhook_endpoints", fields, keyFor(fields));
    settle(res.status);
    if (res.status === 200) {
      setCreated(res.body);
      form.reset();
    } else setError(errorMessage(res));
    await load();
    setBusy(false);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Webhooks</h1>
          <p>Signed event deliveries to your servers, retried with backoff for 72 hours.</p>
        </div>
      </div>

      {created && (
        <div className="notice" role="status">
          <CheckCircle2 size={16} aria-hidden="true" />
          <div>
            Endpoint added. Its signing secret is shown once; store it to verify deliveries.
            <div className="secret">
              <code>{created.secret}</code>
            </div>
          </div>
        </div>
      )}

      <section className="panel add-endpoint">
        <form onSubmit={add} className="inline-form">
          <label className="field grow">
            Endpoint URL
            <input
              className="input"
              name="url"
              type="url"
              placeholder="https://example.com/webhooks"
              required
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? <Spinner /> : <Plus size={15} aria-hidden="true" />}
            Add endpoint
          </button>
        </form>
        {error && (
          <p className="alert inline-error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            {error}
          </p>
        )}
      </section>

      <section className="panel">
        {endpoints && endpoints.length === 0 ? (
          <EmptyState icon={Webhook} title="No endpoints yet">
            Add a URL above and every event on this account is delivered to it, signed with a
            <code> Cleartill-Signature</code> header.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Status</th>
                  <th>Listening to</th>
                  <th>Added</th>
                  <th>
                    <span className="visually-hidden">Open</span>
                  </th>
                </tr>
              </thead>
              {endpoints ? (
                <tbody>
                  {endpoints.map((e) => (
                    <tr key={e.id}>
                      <td>
                        <Link className="row-link" to={`/dashboard/webhooks/${e.id}`}>
                          {e.url}
                        </Link>
                      </td>
                      <td>
                        <StateBadge state={e.status} />
                      </td>
                      <td className="muted">
                        {e.enabled_events.includes("*") ? "All events" : e.enabled_events.join(", ")}
                      </td>
                      <td className="muted">{when(e.created)}</td>
                      <td className="num muted">
                        <ChevronRight size={16} aria-hidden="true" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              ) : (
                <SkeletonRows columns={5} rows={3} />
              )}
            </table>
          </div>
        )}
      </section>
    </>
  );
}
