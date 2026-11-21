import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { dashPost, errorMessage } from "./dash.js";

/* One click: a fresh demo merchant with two weeks of history, logged in (no password). */
export function useDemoAccount() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function start() {
    setBusy(true);
    setError(null);
    const res = await dashPost("/dashboard-api/demo-account");
    if (res.status === 200) return navigate("/dashboard/payments");
    setError(
      res.status === 429
        ? "Lots of demo accounts right now. Try again in a minute."
        : errorMessage(res),
    );
    setBusy(false);
  }
  return { start, busy, error };
}
