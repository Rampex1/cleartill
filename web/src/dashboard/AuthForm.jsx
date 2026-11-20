import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { dashPost, errorMessage } from "./dash.js";

function Fields() {
  return (
    <>
      <label>
        Email
        <input name="email" type="email" autoComplete="email" required />
      </label>
      <label>
        Password
        <input name="password" type="password" autoComplete="current-password" required />
      </label>
    </>
  );
}

export function Login() {
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const res = await dashPost("/dashboard-api/login", {
      email: form.get("email"),
      password: form.get("password"),
    });
    setBusy(false);
    if (res.status === 200) return navigate("/dashboard/payments", { replace: true });
    setError(errorMessage(res));
  }

  return (
    <main className="page">
      <h1>Log in</h1>
      <form onSubmit={submit} className="card-form">
        <Fields />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          Log in
        </button>
      </form>
      <p>
        <Link to="/dashboard/signup">Create an account</Link>
      </p>
    </main>
  );
}

export function Signup() {
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [keys, setKeys] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const res = await dashPost("/dashboard-api/signup", {
      email: form.get("email"),
      password: form.get("password"),
    });
    setBusy(false);
    if (res.status === 200) return setKeys(res.body);
    setError(errorMessage(res));
  }

  if (keys) {
    return (
      <main className="page">
        <h1>Your API keys</h1>
        <p>Copy your secret key now. It won't be shown again.</p>
        <dl className="keys">
          <dt>Secret key</dt>
          <dd>
            <code>{keys.secret_key}</code>
          </dd>
          <dt>Publishable key</dt>
          <dd>
            <code>{keys.publishable_key}</code>
          </dd>
        </dl>
        <button type="button" onClick={() => navigate("/dashboard/payments", { replace: true })}>
          Continue to dashboard
        </button>
      </main>
    );
  }

  return (
    <main className="page">
      <h1>Create an account</h1>
      <form onSubmit={submit} className="card-form">
        <Fields />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          Create account
        </button>
      </form>
      <p>
        <Link to="/dashboard/login">Log in instead</Link>
      </p>
    </main>
  );
}
