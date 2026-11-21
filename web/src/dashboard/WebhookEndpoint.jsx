import { AlertCircle, ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, RefreshCw, Send } from "lucide-react";
import { Fragment, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { EmptyState, SkeletonRows, Spinner, StateBadge, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage } from "./dash.js";

function Attempts({ deliveryId }) {
  const [attempts, setAttempts] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    dashGet(`/dashboard-api/webhook_deliveries/${encodeURIComponent(deliveryId)}/attempts`).then(
      (res) => (res.status === 200 ? setAttempts(res.body.data) : setError(errorMessage(res))),
    );
  }, [deliveryId]);

  if (error) return <p className="muted-text">{error}</p>;
  if (!attempts) return <span className="skeleton" />;
  if (attempts.length === 0) return <p className="muted-text">No attempts yet.</p>;
  return (
    <ol className="attempts">
      {attempts.map((a) => (
        <li key={a.id}>
          <time>{when(a.at)}</time>
          {a.status_code ? (
            <span className={`badge ${a.status_code < 300 ? "badge-success" : "badge-danger"}`}>
              {a.status_code}
            </span>
          ) : (
            <span className="badge badge-danger">{a.error}</span>
          )}
          <span className="muted-text">{a.duration_ms} ms</span>
          <span className="attempt-kind">{a.manual ? "manual" : "automatic"}</span>
        </li>
      ))}
    </ol>
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

  // `isStale` lets a load started for a previous endpoint (or a closed page) drop its result.
  const load = useCallback(
    async (isStale = () => false) => {
      const e = await dashGet(`/v1/webhook_endpoints/${encodeURIComponent(id)}`);
      const res = await dashGet(
        `/dashboard-api/webhook_endpoints/${encodeURIComponent(id)}/deliveries`,
      );
      if (isStale()) return;
      // A deleted endpoint 404s on /v1 but keeps its delivery log; it can't be resent to.
      setEndpoint(e.status === 200 ? e.body : { deleted: true });
      if (res.status === 200) setDeliveries(res.body.data);
      else setError(errorMessage(res));
    },
    [id],
  );

  useEffect(() => {
    let stale = false;
    setEndpoint(null);
    setDeliveries(null);
    setOpen(null);
    setNotice(null);
    setError(null);
    load(() => stale);
    return () => {
      stale = true;
    };
  }, [load]);

  const canResend = endpoint && !endpoint.deleted && endpoint.status === "enabled";

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
      const ok = a && a.status_code >= 200 && a.status_code < 300;
      setNotice({
        ok,
        text: ok
          ? `Resent: ${a.status_code}`
          : `Resent: failed (${a?.status_code ?? a?.error ?? "unknown"})`,
      });
    } else setError(errorMessage(res));
    setOpen(null); // collapse, so a reopened attempts list is fresh
    await load();
    setBusy(null);
  }

  return (
    <>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/dashboard/webhooks">
          <ArrowLeft size={14} aria-hidden="true" />
          All endpoints
        </Link>
      </nav>
      <div className="page-head">
        <div>
          <h1>Webhook endpoint</h1>
          {endpoint && !endpoint.deleted && (
            <p className="row endpoint-meta">
              <code>{endpoint.url}</code>
              <StateBadge state={endpoint.status} />
            </p>
          )}
          {endpoint?.deleted && <p>This endpoint has been deleted; its delivery log is kept.</p>}
          {endpoint && !endpoint.deleted && endpoint.status !== "enabled" && (
            <p>This endpoint is disabled; enable it to resend.</p>
          )}
        </div>
      </div>

      {notice && (
        <p className={notice.ok ? "notice" : "notice notice-warn"} role="status">
          {notice.ok ? <CheckCircle2 size={16} aria-hidden="true" /> : <AlertCircle size={16} aria-hidden="true" />}
          {notice.text}
        </p>
      )}
      {error && (
        <p className="alert" role="alert">
          <AlertCircle size={16} aria-hidden="true" />
          {error}
        </p>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Deliveries</h2>
          <p>Newest first · up to 100</p>
        </div>
        {deliveries && deliveries.length === 0 ? (
          <EmptyState icon={Send} title="No deliveries yet">
            Events created after this endpoint was added are delivered here.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Status</th>
                  {/* `attempts` counts failed sends (each schedules a retry); 0 = first try. */}
                  <th className="num">Retries</th>
                  <th>Next attempt</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              {deliveries ? (
                <tbody>
                  {deliveries.map((d) => (
                    <Fragment key={d.id}>
                      <tr>
                        <td>
                          <code className="event-type">{d.type}</code>
                        </td>
                        <td>
                          <StateBadge state={d.status} />
                        </td>
                        <td className="num">{d.attempts}</td>
                        <td className="muted">
                          {d.status === "pending" ? when(d.next_attempt_at) : "—"}
                        </td>
                        <td className="num">
                          <span className="row row-actions">
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              aria-expanded={open === d.id}
                              aria-controls={`attempts-${d.id}`}
                              aria-label={`Attempts for ${d.type} ${d.id}`}
                              onClick={() => setOpen(open === d.id ? null : d.id)}
                            >
                              {open === d.id ? (
                                <ChevronDown size={14} aria-hidden="true" />
                              ) : (
                                <ChevronRight size={14} aria-hidden="true" />
                              )}
                              Attempts
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              aria-label={`Resend ${d.type} ${d.id}`}
                              disabled={busy !== null || !canResend}
                              onClick={() => resend(d)}
                            >
                              {busy === d.id ? <Spinner /> : <RefreshCw size={13} aria-hidden="true" />}
                              Resend
                            </button>
                          </span>
                        </td>
                      </tr>
                      {open === d.id && (
                        <tr id={`attempts-${d.id}`} className="attempts-row">
                          <td colSpan={5}>
                            <Attempts deliveryId={d.id} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              ) : (
                <SkeletonRows columns={5} rows={4} />
              )}
            </table>
          </div>
        )}
      </section>
    </>
  );
}
