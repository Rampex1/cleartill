import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { dashGet, dashPost, errorMessage, useIdempotencyKey } from "./dash.js";

export default function Webhooks() {
  const [endpoints, setEndpoints] = useState(null);
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [keyFor, settle] = useIdempotencyKey();

  const load = useCallback(async () => {
    // ponytail: first 100 endpoints; page with has_more if a merchant ever has more.
    const res = await dashGet("/v1/webhook_endpoints?limit=100");
    if (res.status === 200) setEndpoints(res.body.data);
    else setError(errorMessage(res));
  }, []);

  useEffect(() => {
    load();
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
      <h1>Webhooks</h1>
      {created && (
        <div className="notice" role="status">
          Signing secret for {created.url} (shown once): <code>{created.secret}</code>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <form onSubmit={add} className="row refund">
        <label>
          Endpoint URL
          <input name="url" type="url" placeholder="https://example.com/webhooks" required />
        </label>
        <button type="submit" disabled={busy}>
          Add endpoint
        </button>
      </form>
      {endpoints && endpoints.length === 0 && <p>No endpoints yet.</p>}
      {endpoints && endpoints.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>URL</th>
              <th>Status</th>
              <th>Events</th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map((e) => (
              <tr key={e.id}>
                <td>
                  <Link to={`/dashboard/webhooks/${e.id}`}>{e.url}</Link>
                </td>
                <td>{e.status}</td>
                <td>{e.enabled_events.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
