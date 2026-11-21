import { AlertCircle, ArrowRight, BookCheck, CheckCircle2, ShieldCheck, Webhook } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ExampleRecord } from "../Home.jsx";
import { Logo, Spinner } from "../ui.jsx";
import { dashPost, errorMessage } from "./dash.js";

function Fields({ newPassword = false }) {
  return (
    <>
      <label className="field">
        Email
        <input className="input" name="email" type="email" autoComplete="email" required />
      </label>
      <label className="field">
        Password
        <input
          className="input"
          name="password"
          type="password"
          autoComplete={newPassword ? "new-password" : "current-password"}
          minLength={newPassword ? 8 : undefined}
          required
        />
        {newPassword && <span className="field-hint">At least 8 characters.</span>}
      </label>
    </>
  );
}

function Aside() {
  return (
    <aside className="auth-aside">
      <h2>A payments API whose ledger you can read.</h2>
      <ul>
        <li>
          <BookCheck size={16} aria-hidden="true" />
          <span>Every capture, refund and payout books balanced debits and credits.</span>
        </li>
        <li>
          <ShieldCheck size={16} aria-hidden="true" />
          <span>Idempotent writes and row locks keep money paths safe under retries.</span>
        </li>
        <li>
          <Webhook size={16} aria-hidden="true" />
          <span>Signed webhooks with retries, a delivery log and one-click resend.</span>
        </li>
      </ul>
      <ExampleRecord />
    </aside>
  );
}

function Shell({ children }) {
  return (
    <div className="auth">
      <div className="auth-form-side">
        <Logo />
        <div className="auth-card">{children}</div>
      </div>
      <Aside />
    </div>
  );
}

function ErrorLine({ error }) {
  return (
    error && (
      <p className="alert" role="alert">
        <AlertCircle size={16} aria-hidden="true" />
        {error}
      </p>
    )
  );
}

export function Login() {
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
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
    <Shell>
      <h1>Log in</h1>
      <p>Welcome back. Your test-mode dashboard is waiting.</p>
      <form onSubmit={submit}>
        <Fields />
        <ErrorLine error={error} />
        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy && <Spinner />}
          Log in
        </button>
      </form>
      <p className="auth-switch">
        New to Cleartill? <Link to="/dashboard/signup">Create an account</Link>
      </p>
    </Shell>
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
    setError(null);
    const form = new FormData(event.currentTarget);
    const res = await dashPost("/dashboard-api/signup", {
      email: form.get("email"),
      password: form.get("password"),
    });
    setBusy(false);
    if (res.status === 200) return setKeys(res.body);
    setError(
      res.status === 0
        ? "Couldn't reach the server. If the account was created, log in and roll your secret key."
        : errorMessage(res),
    );
  }

  if (keys) {
    return (
      <Shell>
        <div className="status-icon">
          <CheckCircle2 size={24} aria-hidden="true" />
        </div>
        <h1>Your API keys</h1>
        <p>Copy your secret key now. For your security it won&apos;t be shown again.</p>
        <dl className="keys-list">
          <div>
            <dt>Secret key</dt>
            <dd>
              <code className="key-box">{keys.secret_key}</code>
            </dd>
          </div>
          <div>
            <dt>Publishable key</dt>
            <dd>
              <code className="key-box">{keys.publishable_key}</code>
            </dd>
          </div>
        </dl>
        <button
          type="button"
          className="btn btn-primary btn-lg btn-block"
          onClick={() => navigate("/dashboard/payments", { replace: true })}
        >
          Continue to dashboard
          <ArrowRight size={16} aria-hidden="true" />
        </button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1>Create an account</h1>
      <p>Test mode only. You&apos;ll get API keys and a dashboard right away.</p>
      <form onSubmit={submit}>
        <Fields newPassword />
        <ErrorLine error={error} />
        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy && <Spinner />}
          Create account
        </button>
      </form>
      <p className="auth-switch">
        Already have an account? <Link to="/dashboard/login">Log in instead</Link>
      </p>
    </Shell>
  );
}
