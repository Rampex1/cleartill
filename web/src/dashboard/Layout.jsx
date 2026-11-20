import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { dashGet, dashPost, errorMessage, onUnauthorized } from "./dash.js";

export default function Layout() {
  const navigate = useNavigate();
  const [merchant, setMerchant] = useState(null);
  const [error, setError] = useState(null);

  const check = useCallback(async () => {
    setError(null);
    const res = await dashGet("/dashboard-api/session");
    if (res.status === 200) setMerchant(res.body.merchant);
    else if (res.status !== 401) setError(errorMessage(res)); // 401: onUnauthorized below
  }, []);

  useEffect(() => {
    const stop = onUnauthorized(() => navigate("/dashboard/login", { replace: true }));
    check();
    return stop;
  }, [check, navigate]);

  async function logout() {
    const res = await dashPost("/dashboard-api/logout");
    // Only the server can clear the HttpOnly cookie: don't look logged out unless it did.
    if (res.status === 200) navigate("/dashboard/login", { replace: true });
    else setError(`Logout failed. ${errorMessage(res)}`);
  }

  if (!merchant) {
    return (
      <main className="wide">
        {error ? (
          <>
            <p className="error" role="alert">
              {error}
            </p>
            <button type="button" onClick={check}>
              Try again
            </button>
          </>
        ) : (
          <p>Loading…</p>
        )}
      </main>
    );
  }
  return (
    <div className="dash">
      <nav className="nav">
        <strong>Cleartill</strong>
        <NavLink to="/dashboard/payments">Payments</NavLink>
        <NavLink to="/dashboard/customers">Customers</NavLink>
        <NavLink to="/dashboard/balance">Balance</NavLink>
        <NavLink to="/dashboard/webhooks">Webhooks</NavLink>
        <NavLink to="/dashboard/keys">API keys</NavLink>
        <span className="spacer">{merchant.email}</span>
        <button type="button" className="secondary" onClick={logout}>
          Log out
        </button>
      </nav>
      <main className="wide">
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <Outlet />
      </main>
    </div>
  );
}
