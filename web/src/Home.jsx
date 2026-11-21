import { ArrowRight, BookOpen, CheckCircle2, CreditCard, GitBranch, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { postForm } from "./api.js";
import { Logo, Money, Spinner } from "./ui.jsx";

const REPO = "https://github.com/Rampex1/cleartill";

/* A synthetic payment record, labelled as an example: the hero shows what the dashboard shows. */
const EXAMPLE = [
  {
    title: "Authorized",
    time: "09:41:02",
    tone: "info",
    Icon: CreditCard,
    note: "Visa •••• 4242 approved for $48.00",
  },
  {
    title: "Captured",
    time: "09:41:09",
    tone: "success",
    Icon: CheckCircle2,
    ledger: [
      ["Card network clearing", 4800],
      ["Merchant pending", -4631],
      ["Platform fees", -169],
    ],
  },
  {
    title: "Partially refunded",
    time: "11:02:37",
    tone: "neutral",
    Icon: RotateCcw,
    ledger: [
      ["Merchant available", 1200],
      ["Card network clearing", -1200],
    ],
  },
];

export function useDemoCheckout() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function start() {
    setBusy(true);
    setError(null);
    const res = await fetch("/checkout-api/demo", {
      method: "POST",
      headers: { "X-Cleartill-Dashboard": "1" },
    }).catch(() => null);
    const body = res ? await res.json().catch(() => ({})) : {};
    if (res?.ok && body.url) return window.location.assign(body.url);
    setError(
      res?.status === 429
        ? "Lots of demos right now. Try again in a minute."
        : "Couldn't start the demo. Please try again.",
    );
    setBusy(false);
  }
  return { start, busy, error };
}

function Ledger({ rows }) {
  return (
    <div className="ledger">
      <table>
        <thead>
          <tr>
            <th>Account</th>
            <th className="num">Debit</th>
            <th className="num">Credit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([account, amount]) => (
            <tr key={account}>
              <td>{account}</td>
              <td className="num" data-side="Dr">{amount > 0 ? <Money amount={amount} /> : ""}</td>
              <td className="num" data-side="Cr">{amount < 0 ? <Money amount={-amount} /> : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ExampleRecord() {
  return (
    <figure className="record" aria-label="Example payment record">
      <div className="record-head">
        <div>
          <Money amount={4800} display showCurrency />
          <span className="id">pi_3QxN8m2v6Lr4cKt7</span>
        </div>
        <span className="record-example">Example</span>
      </div>
      <div className="record-body">
        <ol className="timeline">
          {EXAMPLE.map(({ title, time, tone, Icon, note, ledger }) => (
            <li className="timeline-step" key={title}>
              <span className={`timeline-dot tone-${tone}`}>
                <Icon size={14} strokeWidth={2.25} aria-hidden="true" />
              </span>
              <div className="timeline-body">
                <div className="timeline-title">
                  <strong>{title}</strong>
                  <time>{time}</time>
                </div>
                {note && <p className="timeline-events">{note}</p>}
                {ledger && <Ledger rows={ledger} />}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </figure>
  );
}

export default function Home() {
  const demo = useDemoCheckout();
  return (
    <>
      <header className="site-nav">
        <div className="site-nav-inner">
          <Logo />
          <nav className="site-links" aria-label="Site">
            <a href="#how-it-works">How it works</a>
            <a href="/docs">API reference</a>
            <a href={REPO}>Source</a>
          </nav>
          <div className="site-nav-actions">
            <Link className="btn btn-ghost" to="/dashboard/login">
              Log in
            </Link>
            <Link className="btn btn-secondary" to="/dashboard/signup">
              Sign up
            </Link>
          </div>
        </div>
      </header>

      <main className="landing">
        <section className="hero">
          <div>
            <h1>Payments you can read all the way down.</h1>
            <p className="hero-lede">
              Cleartill takes card payments through an API, a hosted checkout and a Python SDK,
              and books every cent in a double-entry ledger you can inspect, step by step.
            </p>
            <div className="hero-actions">
              <button
                type="button"
                className="btn btn-primary btn-lg"
                onClick={demo.start}
                disabled={demo.busy}
              >
                {demo.busy ? <Spinner /> : <CreditCard size={17} aria-hidden="true" />}
                Try a live checkout
              </button>
              <Link className="btn btn-secondary btn-lg" to="/dashboard/signup">
                Create an account
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            </div>
            {demo.error ? (
              <p className="alert hero-note" role="alert">
                {demo.error}
              </p>
            ) : (
              <p className="hero-note hero-note-text">
                A real $30.00 test payment. Pay with <code>4242 4242 4242 4242</code>, any future
                date, any CVC.
              </p>
            )}
          </div>
          <ExampleRecord />
        </section>

        <section className="section" id="how-it-works">
          <div className="section-head">
            <h2>A simulated card network. Everything else is real.</h2>
            <p>
              Only the card network and 3D Secure are simulated. The state machine, the
              idempotency, the locking, the ledger and the webhooks are the actual product, the
              same code path a merchant&apos;s integration would hit.
            </p>
          </div>
          <div className="proof-grid">
            <article className="proof">
              <div className="proof-text">
                <h3>An SDK that retries safely</h3>
                <p>
                  Every POST carries an idempotency key that survives retries, so a timeout can
                  never charge or refund twice. Errors arrive as typed exceptions.
                </p>
              </div>
              <div className="proof-figure">
                <pre className="code">
                  <span className="c-key">import</span> cleartill{"\n\n"}
                  client = cleartill.Client(<span className="c-str">&quot;sk_test_…&quot;</span>)
                  {"\n"}pi = client.payment_intents.create({"\n"}
                  {"    "}amount=<span className="c-str">2000</span>, currency=
                  <span className="c-str">&quot;usd&quot;</span>,{"\n"}
                  {"    "}payment_method=<span className="c-str">&quot;pm_card_visa&quot;</span>,
                  confirm=<span className="c-key">True</span>,{"\n"}){"\n"}
                  client.refunds.create(payment_intent=pi[
                  <span className="c-str">&quot;id&quot;</span>], amount=
                  <span className="c-str">500</span>){"\n"}
                  <span className="c-com"># retried POSTs reuse one Idempotency-Key</span>
                </pre>
              </div>
            </article>
            <article className="proof">
              <div className="proof-text">
                <h3>Webhooks with a delivery log</h3>
                <p>
                  Signed, retried with backoff for 72 hours and guarded against SSRF. Every
                  attempt is logged and can be resent from the dashboard.
                </p>
              </div>
              <div className="proof-figure">
                <ol className="log" aria-label="Example webhook attempts">
                  <li>
                    <time>09:41:10</time>
                    <span>payment_intent.succeeded</span>
                    <span className="badge badge-danger">503</span>
                  </li>
                  <li>
                    <time>09:42:10</time>
                    <span>retry 1 · backoff 60s</span>
                    <span className="badge badge-danger">503</span>
                  </li>
                  <li>
                    <time>09:47:10</time>
                    <span>retry 2 · backoff 5m</span>
                    <span className="badge badge-success">200</span>
                  </li>
                </ol>
              </div>
            </article>
          </div>
        </section>

        <section className="section">
          <div className="section-head">
            <h2>Test cards</h2>
            <p>
              Outcomes come from the card number, so every path is one checkout away. Use any
              future expiry date and any CVC.
            </p>
          </div>
          <div className="cards-strip">
            <div className="test-card">
              <code>4242 4242 4242 4242</code>
              <p>Succeeds and settles into the ledger.</p>
            </div>
            <div className="test-card">
              <code>4000 0000 0000 0002</code>
              <p>Declined by the issuer, with a reason code.</p>
            </div>
            <div className="test-card">
              <code>4000 0027 6000 3184</code>
              <p>Asks for 3D Secure before approving.</p>
            </div>
          </div>
        </section>

        <section className="cta-band">
          <div>
            <h2>See a payment through.</h2>
            <p>Pay the demo checkout, then open the dashboard to follow it.</p>
          </div>
          <div className="row">
            <button
              type="button"
              className="btn btn-primary btn-lg"
              onClick={demo.start}
              disabled={demo.busy}
            >
              Start the demo
            </button>
            <a className="btn btn-secondary btn-lg" href="/docs">
              <BookOpen size={16} aria-hidden="true" />
              API reference
            </a>
          </div>
        </section>
      </main>

      <footer className="site-foot">
        <div className="site-foot-inner">
          <p>
            Cleartill is an independent learning project in test mode. It is not affiliated with
            any payments company, and no real money moves.
          </p>
          <a href={REPO} className="row">
            <GitBranch size={15} aria-hidden="true" />
            Source on GitHub
          </a>
        </div>
      </footer>
    </>
  );
}
