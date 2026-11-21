import {
  Banknote,
  CheckCircle2,
  CircleDashed,
  Clock3,
  RotateCcw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { Money, Token, fullTime, when } from "./ui.jsx";

/* A payment's lifecycle with the ledger postings behind each step. Shared by the dashboard's
   payment detail and the public landing-page demo receipt. */

const ACCOUNTS = {
  card_network_clearing: ["Card network clearing", "platform"],
  platform_fees: ["Platform fees", "platform"],
  payouts_in_transit: ["Payouts in transit", "platform"],
  merchant_pending: ["Pending balance", "yours"],
  merchant_available: ["Available balance", "yours"],
};

/* Which event best names a step, in priority order. */
const STEP_TITLES = [
  ["payment_intent.payment_failed", "Payment failed", "danger", XCircle],
  ["charge.failed", "Payment failed", "danger", XCircle],
  ["refund.created", "Refund issued", "neutral", RotateCcw],
  ["charge.captured", "Captured", "success", CheckCircle2],
  ["payment_intent.succeeded", "Payment succeeded", "success", CheckCircle2],
  ["payment_intent.amount_capturable_updated", "Authorized", "info", Clock3],
  ["charge.succeeded", "Charge succeeded", "success", CheckCircle2],
  ["payment_intent.requires_action", "Authentication requested", "warning", ShieldAlert],
  ["payment_intent.canceled", "Canceled", "neutral", XCircle],
  ["payment_intent.created", "Payment created", "neutral", CircleDashed],
];

const POSTING_TITLES = { release: "Funds became available" };

/* Group events emitted by one API request into one step (worker events stand alone), then
   pin each journal posting to the step that caused it: captures to the capture, refunds to
   their refund. */
function buildSteps(timeline) {
  const steps = [];
  for (const event of timeline.events) {
    const last = steps.at(-1);
    // Same API request, or (worker/seeded events have no request) the same instant.
    const together =
      last &&
      (event.request
        ? last.request === event.request
        : !last.request && last.created === event.created && last.events.length > 0);
    if (together) last.events.push(event.type);
    else {
      steps.push({
        created: event.created,
        request: event.request,
        events: [event.type],
        postings: [],
      });
    }
  }
  for (const step of steps) {
    const match = STEP_TITLES.find(([type]) => step.events.includes(type));
    [, step.title, step.tone, step.Icon] = match ?? [null, step.events[0], "neutral", CircleDashed];
  }
  const refundSteps = steps.filter((s) => s.events.includes("refund.created"));
  const captureStep =
    steps.find((s) => s.events.includes("charge.captured")) ??
    steps.find((s) => s.events.includes("payment_intent.succeeded"));
  for (const posting of timeline.postings) {
    let target = null;
    if (posting.type === "charge") target = captureStep;
    else if (posting.type === "refund") target = refundSteps.shift();
    if (target) target.postings.push(posting);
    else {
      steps.push({
        created: posting.created,
        events: [],
        postings: [posting],
        title: POSTING_TITLES[posting.type] ?? posting.type,
        tone: "success",
        Icon: Banknote,
      });
    }
  }
  return steps.sort((a, b) => a.created - b.created);
}

function Posting({ posting }) {
  return (
    <div className="ledger">
      <div className="ledger-head">
        <span>Ledger · {posting.type.replace("_", " ")}</span>
        <span className="id">{posting.id}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Account</th>
            <th className="num">Debit</th>
            <th className="num">Credit</th>
          </tr>
        </thead>
        <tbody>
          {posting.entries.map((entry, i) => {
            const [name, owner] = ACCOUNTS[entry.account] ?? [entry.account, ""];
            return (
              <tr key={i}>
                <td>
                  <span className="account-name">
                    {name}
                    <small>{owner}</small>
                  </span>
                </td>
                <td className="num" data-side="Dr">
                  {entry.amount > 0 ? <Money amount={entry.amount} currency={entry.currency} /> : ""}
                </td>
                <td className="num" data-side="Cr">
                  {entry.amount < 0 ? (
                    <Money amount={-entry.amount} currency={entry.currency} />
                  ) : (
                    ""
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const HOLD_SECONDS = 7 * 24 * 3600; // uncaptured authorizations expire after 7 days

export function Timeline({ timeline, pi = null }) {
  if (!timeline) {
    return (
      <div className="stack-sm" aria-hidden="true">
        <span className="skeleton" />
        <span className="skeleton" />
        <span className="skeleton" />
      </div>
    );
  }
  const steps = buildSteps(timeline);
  if (pi?.status === "requires_capture") {
    const authorized = steps.find((s) => s.events.includes("charge.succeeded"));
    steps.push({
      created: null,
      events: [],
      postings: [],
      title: "Capture",
      tone: "pending",
      Icon: Clock3,
      ghost: authorized
        ? `Waiting for you. The hold expires ${when(authorized.created + HOLD_SECONDS)} if not captured.`
        : "Waiting for you to capture the authorized amount.",
    });
  }
  return (
    <div className="timeline">
      {steps.map((step, i) => {
        const Icon = step.Icon;
        return (
          <div className="timeline-step" key={i}>
            <span className={`timeline-dot tone-${step.tone}`}>
              <Icon size={14} strokeWidth={2.25} aria-hidden="true" />
            </span>
            <div className="timeline-body">
              <div className="timeline-title">
                <strong>{step.title}</strong>
                {step.created !== null && (
                  <time
                    dateTime={new Date(step.created * 1000).toISOString()}
                    title={fullTime(step.created)}
                  >
                    {when(step.created)}
                  </time>
                )}
              </div>
              {step.events.length > 0 && (
                <p className="timeline-events mono">
                  {step.events.map((type, i) => (
                    <span key={type + i}>
                      {i > 0 && " · "}
                      <Token>{type}</Token>
                    </span>
                  ))}
                </p>
              )}
              {step.ghost && <p className="timeline-events">{step.ghost}</p>}
              {step.postings.map((p) => (
                <Posting key={p.id} posting={p} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

