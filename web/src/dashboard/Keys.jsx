import { AlertCircle, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Spinner, StateBadge, when } from "../ui.jsx";
import { dashGet, dashPost, errorMessage } from "./dash.js";

const KIND = {
  secret: ["Secret key", "Server-side only. Creates payments, refunds and payouts."],
  publishable: ["Publishable key", "Safe in browsers. Can only tokenize cards."],
};

export default function Keys() {
  const [keys, setKeys] = useState([]);
  const [fresh, setFresh] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    const res = await dashGet("/dashboard-api/keys");
    if (res.status === 200) setKeys(res.body.data);
    else setError(errorMessage(res));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function roll(key) {
    setBusy(key.id);
    setError(null);
    // No Idempotency-Key: the response holds a live secret and is never stored for replay.
    // A double click is still safe: the old key is revoked, so the second roll gets 404.
    const res = await dashPost(`/dashboard-api/keys/${encodeURIComponent(key.id)}/roll`);
    if (res.status === 200) setFresh(res.body);
    else if (res.status === 0) {
      setError(
        "Couldn't reach the server. The key may have been rolled: if its last 4 changed below, " +
          "roll it again to see a new secret.",
      );
    } else setError(errorMessage(res));
    await load();
    setBusy(null);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>API keys</h1>
          <p>Keys authenticate requests to the Cleartill API. Rolling one revokes it at once.</p>
        </div>
      </div>

      {fresh && (
        <div className="notice" role="status">
          <ShieldCheck size={16} aria-hidden="true" />
          <div>
            New {fresh.kind} key, shown once. Update your integration before you leave this page.
            <div className="secret">
              <code>{fresh.secret}</code>
            </div>
          </div>
        </div>
      )}
      {error && (
        <p className="alert" role="alert">
          <AlertCircle size={16} aria-hidden="true" />
          {error}
        </p>
      )}

      <section className="panel">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Token</th>
                <th>Status</th>
                <th>Created</th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const [name, hint] = KIND[k.kind] ?? [k.kind, ""];
                return (
                  <tr key={k.id}>
                    <td>
                      <span className="key-name">
                        <KeyRound size={15} aria-hidden="true" />
                        <span>
                          <strong>{name}</strong>
                          <small>{hint}</small>
                        </span>
                      </span>
                    </td>
                    <td>
                      <code>
                        {k.kind === "secret" ? "sk_test_" : "pk_test_"}…{k.last4}
                      </code>
                    </td>
                    <td>
                      <StateBadge state="enabled" />
                    </td>
                    <td className="muted">{when(k.created)}</td>
                    <td className="num">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={busy !== null}
                        onClick={() => roll(k)}
                      >
                        {busy === k.id ? <Spinner /> : <RefreshCw size={13} aria-hidden="true" />}
                        Roll {k.kind} key
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
