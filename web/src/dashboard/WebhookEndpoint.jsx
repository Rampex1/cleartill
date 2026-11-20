import { Fragment, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { dashGet, dashPost, errorMessage } from "./dash.js";
import { date } from "./Payments.jsx";

function Attempts({ deliveryId }) {
  const [attempts, setAttempts] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    dashGet(`/dashboard-api/webhook_deliveries/${encodeURIComponent(deliveryId)}/attempts`).then(
      (res) => (res.status === 200 ? setAttempts(res.body.data) : setError(errorMessage(res))),
    );
  }, [deliveryId]);

  if (error) return <p className="error">{error}</p>;
  if (!attempts) return <p>Loading…</p>;
  if (attempts.length === 0) return <p>No attempts yet.</p>;
  return (
    <ul className="attempts">
      {attempts.map((a) => (
        <li key={a.id}>
          {date(a.at)}: {a.status_code ?? a.error} in {a.duration_ms} ms
          {a.manual ? " (manual)" : ""}
        </li>
      ))}
    </ul>
  );
}

export default function WebhookEndpoint() {
  const { id } = useParams();
  const [endpoint, setEndpoint] = useState(null);
  const [deliveries, setDeliveries] = useState(null);
  const [open, setOpen] = useState(null);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    const e = await dashGet(`/v1/webhook_endpoints/${encodeURIComponent(id)}`);
    if (e.status === 200) setEndpoint(e.body);
    const res = await dashGet(`/dashboard-api/webhook_endpoints/${encodeURIComponent(id)}/deliveries`);
    if (res.status === 200) setDeliveries(res.body.data);
    else setError(errorMessage(res));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function resend(delivery) {
    setBusy(delivery.id);
    setError(null);
    setNotice(null);
    // No Idempotency-Key: a resend is meant to be repeatable (each one is a new attempt).
    const res = await dashPost(
      `/dashboard-api/webhook_deliveries/${encodeURIComponent(delivery.id)}/resend`,
    );
    if (res.status === 200) {
      const a = res.body.attempt;
      setNotice(a?.status_code ? `Resent: ${a.status_code}` : `Resent: failed (${a?.error})`);
    } else setError(errorMessage(res));
    setOpen(null); // collapse, so a reopened attempts list is fresh
    await load();
    setBusy(null);
  }

  return (
    <>
      <p>
        <Link to="/dashboard/webhooks">← Webhooks</Link>
      </p>
      <h1>Webhook endpoint</h1>
      {endpoint && (
        <p>
          <code>{endpoint.url}</code> ({endpoint.status})
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {deliveries && deliveries.length === 0 && <p>No deliveries yet.</p>}
      {deliveries && deliveries.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Event</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Next attempt</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {deliveries.map((d) => (
              <Fragment key={d.id}>
                <tr>
                  <td>{d.type}</td>
                  <td>{d.status}</td>
                  <td>{d.attempts}</td>
                  <td>{d.status === "pending" ? date(d.next_attempt_at) : "—"}</td>
                  <td className="row">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setOpen(open === d.id ? null : d.id)}
                    >
                      Attempts
                    </button>
                    <button type="button" disabled={busy !== null} onClick={() => resend(d)}>
                      Resend
                    </button>
                  </td>
                </tr>
                {open === d.id && (
                  <tr>
                    <td colSpan={5}>
                      <Attempts deliveryId={d.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
