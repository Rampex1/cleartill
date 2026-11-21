import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock3,
  ShieldAlert,
  Undo2,
  XCircle,
} from "lucide-react";
import { Link } from "react-router-dom";

/* The mark: a rounded register with an open till-drawer slot. */
export function LogoMark({ size = 24 }) {
  return (
    <svg
      className="logo-mark"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <rect x="1" y="1" width="22" height="22" rx="6.5" fill="#0d6b63" />
      <rect x="5.5" y="6" width="13" height="2.6" rx="1.3" fill="#ffffff" />
      <rect x="5.5" y="11" width="8" height="2.6" rx="1.3" fill="#ffffff" opacity="0.72" />
      <rect x="5.5" y="16" width="13" height="2.2" rx="1.1" fill="#9fd5cd" />
    </svg>
  );
}

export function Logo({ to = "/", size }) {
  return (
    <Link className="logo" to={to}>
      <LogoMark size={size} />
      Cleartill
    </Link>
  );
}

/* Statement-style figure: dollars dominant, cents subordinate. Text content stays the plain
   formatted amount ("$14.12"), so copy, search and screen readers read it normally. */
export function Money({ amount, currency = "usd", display = false, showCurrency = false }) {
  const parts = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
  }).formatToParts(amount / 100);
  let major = "";
  let minor = "";
  let seenDecimal = false;
  for (const part of parts) {
    if (part.type === "decimal") seenDecimal = true;
    if (seenDecimal) minor += part.value;
    else major += part.value;
  }
  const classes = ["money", display && "money-display", amount < 0 && "money-negative"];
  return (
    <span className={classes.filter(Boolean).join(" ")}>
      <span className="money-major">{major}</span>
      <span className="money-minor">{minor}</span>
      {showCurrency && <span className="money-currency">{currency}</span>}
    </span>
  );
}

export function money(amount, currency = "usd") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(amount / 100);
}

/* Payment intent statuses as merchants read them; the raw API value stays on the detail page. */
const PI_STATUS = {
  succeeded: { label: "Succeeded", tone: "success", Icon: CheckCircle2 },
  requires_capture: { label: "Uncaptured", tone: "info", Icon: Clock3 },
  requires_action: { label: "Needs authentication", tone: "warning", Icon: ShieldAlert },
  requires_confirmation: { label: "Incomplete", tone: "neutral", Icon: CircleDashed },
  requires_payment_method: { label: "Incomplete", tone: "neutral", Icon: CircleDashed },
  canceled: { label: "Canceled", tone: "neutral", Icon: Ban },
  processing: { label: "Processing", tone: "info", Icon: Clock3 },
};

export function PaymentStatus({ status, refunded = false, failed = false }) {
  let meta = PI_STATUS[status] ?? { label: status, tone: "neutral", Icon: CircleDashed };
  if (refunded) meta = { label: "Refunded", tone: "neutral", Icon: Undo2 };
  else if (failed && status === "requires_payment_method") {
    meta = { label: "Failed", tone: "danger", Icon: XCircle };
  }
  const { label, tone, Icon } = meta;
  return (
    <span className={`badge badge-${tone}`}>
      <Icon size={13} strokeWidth={2.25} aria-hidden="true" />
      {label}
    </span>
  );
}

/* Delivery, payout and key states: the DOM keeps the API's lowercase word (CSS capitalizes). */
const STATE = {
  succeeded: { tone: "success", Icon: CheckCircle2 },
  paid: { tone: "success", Icon: CheckCircle2 },
  enabled: { tone: "success", Icon: CheckCircle2 },
  available: { tone: "success", Icon: CheckCircle2 },
  pending: { tone: "warning", Icon: Clock3 },
  failed: { tone: "danger", Icon: XCircle },
  disabled: { tone: "neutral", Icon: Ban },
};

export function StateBadge({ state }) {
  const { tone, Icon } = STATE[state] ?? { tone: "neutral", Icon: AlertTriangle };
  return (
    <span className={`badge badge-state badge-${tone}`}>
      <Icon size={13} strokeWidth={2.25} aria-hidden="true" />
      {state}
    </span>
  );
}

export function EmptyState({ icon: Icon, title, children, action }) {
  return (
    <div className="empty">
      {Icon && (
        <div className="empty-icon">
          <Icon size={20} aria-hidden="true" />
        </div>
      )}
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function SkeletonRows({ rows = 5, columns = 4 }) {
  return (
    <tbody aria-hidden="true">
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r}>
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              <span className="skeleton" />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/* An identifier that may wrap only after "." or "_" (never mid-token). */
export function Token({ children }) {
  const parts = String(children).split(/(?<=[._])/);
  return (
    <span className="token">
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && <wbr />}
        </span>
      ))}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

const DATE = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const DATE_YEAR = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const TIME = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

export function when(unix) {
  const d = new Date(unix * 1000);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return `${(sameYear ? DATE : DATE_YEAR).format(d)}, ${TIME.format(d)}`;
}

export function fullTime(unix) {
  return new Date(unix * 1000).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}
