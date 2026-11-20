import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { dashGet, dashPost } from "./dash.js";

export default function Layout() {
  const navigate = useNavigate();
  const [merchant, setMerchant] = useState(null);

  useEffect(() => {
    dashGet("/dashboard-api/session").then(({ status, body }) => {
      if (status === 200) setMerchant(body.merchant);
      else navigate("/dashboard/login", { replace: true });
    });
  }, [navigate]);

  async function logout() {
    await dashPost("/dashboard-api/logout");
    navigate("/dashboard/login", { replace: true });
  }

  if (!merchant) return <main className="wide"><p>Loading…</p></main>;
  return (
    <div className="dash">
      <nav className="nav">
        <strong>Cleartill</strong>
        <NavLink to="/dashboard/payments">Payments</NavLink>
        <NavLink to="/dashboard/keys">API keys</NavLink>
        <span className="spacer">{merchant.email}</span>
        <button type="button" className="secondary" onClick={logout}>
          Log out
        </button>
      </nav>
      <main className="wide">
        <Outlet />
      </main>
    </div>
  );
}
