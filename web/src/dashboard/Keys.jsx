import { useCallback, useEffect, useState } from "react";

import { dashGet, dashPost, errorMessage } from "./dash.js";

export default function Keys() {
  const [keys, setKeys] = useState([]);
  const [fresh, setFresh] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const res = await dashGet("/dashboard-api/keys");
    if (res.status === 200) setKeys(res.body.data);
    else setError(errorMessage(res));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function roll(key) {
    setBusy(true);
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
    setBusy(false);
  }

  return (
    <>
      <h1>API keys</h1>
      {fresh && (
        <div className="notice" role="status">
          New {fresh.kind} key (shown once): <code>{fresh.secret}</code>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <table>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id}>
              <td>{k.kind}</td>
              <td>
                <code>…{k.last4}</code>
              </td>
              <td>
                <button type="button" disabled={busy} onClick={() => roll(k)}>
                  Roll {k.kind} key
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
