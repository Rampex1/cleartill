import { Banknote, CreditCard, KeyRound, LogOut, Users, Webhook } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { Logo } from "../ui.jsx";
import { dashGet, dashPost, errorMessage, onUnauthorized } from "./dash.js";

const PRIMARY = [
  ["/dashboard/payments", "Payments", CreditCard],
  ["/dashboard/customers", "Customers", Users],
  ["/dashboard/balance", "Balance", Banknote],
];
const DEVELOPERS = [
  ["/dashboard/webhooks", "Webhooks", Webhook],
  ["/dashboard/keys", "API keys", KeyRound],
];

function NavItems({ items }) {
  return items.map(([to, label, Icon]) => (
    <NavLink key={to} to={to}>
      <Icon size={17} strokeWidth={2} aria-hidden="true" />
      {label}
    </NavLink>
  ));
}

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
      <main className="center-page">
        {error ? (
          <div className="center-card">
            <h1>Dashboard unavailable</h1>
            <p className="alert" role="alert">
              {error}
            </p>
            <p className="hero-actions">
              <button type="button" className="btn btn-primary" onClick={check}>
                Try again
              </button>
            </p>
          </div>
        ) : (
          <span className="skeleton loading-bar" aria-label="Loading" />
        )}
      </main>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <Logo to="/dashboard/payments" />
        <nav className="nav" aria-label="Dashboard">
          <NavItems items={PRIMARY} />
          <div className="nav-label">Developers</div>
          <NavItems items={DEVELOPERS} />
        </nav>
        <div className="sidebar-foot">
          <div className="account">
            <span className="avatar" aria-hidden="true">
              {merchant.email.charAt(0)}
            </span>
            <span className="account-meta">
              <strong>Test account</strong>
              <span>{merchant.email}</span>
            </span>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={logout}>
            <LogOut size={14} aria-hidden="true" />
            Log out
          </button>
        </div>
      </aside>
      <main className="main">
        <div className="main-inner">
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          <Outlet />
        </div>
      </main>
    </div>
  );
}
