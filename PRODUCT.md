# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Primary: portfolio reviewers (recruiters and engineers) judging whether Cleartill is a credible,
shipped payments product. They arrive from a link or the README, sign up, click through the
dashboard and pay a hosted checkout with a test card, usually in a few minutes.

Inside the product, the people modelled are merchants (a developer or small-business operator
taking card payments: checking payments, capturing and refunding, watching the balance, paying
out, wiring webhooks and API keys) and their customers (paying on the hosted checkout, sometimes
through a 3D Secure step).

## Product Purpose
A Stripe-style payments API built as a learning project: merchants take card payments through an
API, a hosted checkout page and a Python SDK, and every cent moves through a double-entry ledger.
Success is a reviewer concluding, quickly, that this is real engineering presented with the care
of a business that would handle money.

## Positioning
Not a mock: the money paths are real. PaymentIntents, captures, refunds, payouts, idempotency
keys, row locking, a ledger whose invariants Postgres enforces, signed webhooks with retries and
an SSRF guard. Only the card network and 3D Secure are simulated. The interface should show that
depth (ledger-backed balances, delivery logs, idempotent actions) rather than hide it.

## Operating Context
- Runs locally via `docker compose up`; the API serves the SPA from the same origin.
- Test mode only. Every page carries the banner "Test mode: simulated payments, never enter a
  real card." Test cards: 4242 4242 4242 4242 succeeds, 4000 0000 0000 0002 declines,
  4000 0027 6000 3184 asks for 3D Secure.
- Demo availability delay is 5 minutes, so pending → available → payout is observable in a
  session.
- Surfaces: the merchant dashboard (login, signup with keys shown once, payments list and detail
  with capture/refund, customers, balance with payouts, webhooks with deliveries/attempts/resend,
  API keys with roll), the hosted checkout and 3DS pages, and a public landing page at `/`.

## Capabilities and Constraints
- React SPA (Vite, React Router, plain JSX) in `web/`; one stylesheet today (`web/src/styles.css`).
- Strict CSP on every response: `default-src 'self'; script-src 'self'; style-src 'self'`. No
  inline scripts or styles, no external fonts, CDNs or images; every asset ships from `web/`.
- Amounts are integer cents in USD; the API is form-encoded and Stripe-shaped.
- Playwright e2e tests (`api/tests/e2e/`) pin accessible names: labels ("Card number", "Expiry
  month", "Expiry year", "CVC", "Email", "Password", "Refund amount", "Endpoint URL", "Payout
  amount (cents)"), buttons ("Pay $30.00", "Log in", "Create account", "Capture", "Refund",
  "Add endpoint", "Pay out", "Resend", "Attempts", "Roll secret key", "Complete authentication",
  "Fail authentication", "Log out", "Continue to dashboard"), headings ("Payments", "Payment",
  "Customers", "Balance", "Webhooks", "Webhook endpoint", "API keys", "Your API keys", "Create an
  account", "Payment complete"), nav links, `role="alert"` for errors and `role="status"` for
  one-time secrets. Redesigns keep these names.
- Idempotency-Keys are sent per action from the dashboard; double submits must stay harmless.

## Brand Commitments
- Name: Cleartill. Nothing else fixed.
- Must not imitate Stripe's look, name, logo or trademarks; it is an independent learning project
  and must not read as Stripe or as phishing.
- Standing direction (user choice, 2026-11-21): the category standard, a clean conventional
  fintech product executed at full craft, chosen over expressive worlds. Craft bar: Mercury.
  Match its level of polish (calm, premium, generous spacing, money figures that read instantly),
  never its look.

## Evidence on Hand
- No customers, testimonials, volumes, uptime figures or pricing exist. Never fabricate them.
- Real, demonstrable facts: the feature list above, the test suite (1,000+ API tests,
  Playwright flows), CI jobs, the SDK, `scripts/smoke.py`, the architecture in `README.md`.
- No logo, illustration or photography assets exist yet.

## Product Principles
1. Honest about being a simulation: test mode is always visible, never disguised.
2. Show the machinery: ledger, idempotency and delivery logs are features, surfaced plainly.
3. Money is never ambiguous: amounts, states and what an action will do are explicit before it
   happens.
4. Credible at a glance, dependable on inspection: the first screen persuades, the details hold.

## Accessibility & Inclusion
Keep the accessible names above stable; WCAG 2.1 AA contrast and keyboard operation across the
dashboard and checkout. Respect reduced motion.
