---
version: 1
slug: "web-src"
primary_target: "web/src"
related_targets: ["web/src/dashboard","web/src/Checkout.jsx","web/src/ThreeDS.jsx","web/src/Home.jsx"]
---

# Cleartill web app (landing, dashboard, hosted checkout)

Scope: `/` landing (Persuade), `/dashboard/*` (Operate), `/checkout/*` and 3DS (Operate). Audience: portfolio reviewers clicking through in minutes. Landing action: try a live checkout without an account. Showcase screen: payment detail. Failure to avoid: too plain. Craft bar: Mercury (polish, not look). Keep every accessible name the e2e tests pin (see PRODUCT.md).

## Direction contract

THESIS: The category standard, done at full craft: a calm, precise fintech product where money is the loudest thing on every screen. Refuses the generic SaaS template (gradient hero, icon-tile rows, glassy cards) and anything that reads as Stripe.

OWN-WORLD: Light, faintly green-grey neutrals (ground #f7f8f7, panels white, sidebar #f0f2f1), ink #15201c, one Cleartill teal #0d6b63 for actions, selection and focus only; status colours with glyph plus word. Inter (tabular figures) for UI, Schibsted Grotesk for headings. 8px grid, 10px radii, hairline borders, soft offset shadows. Logo: a rounded square with a till-drawer slot.

STORY: A reviewer lands, pays a real demo checkout in one click, sees "it actually works", signs up, opens a payment and reads its whole life (authorized → captured → refunded) with the ledger postings behind each step.

FIRST VIEWPORT: Landing: top nav; left, headline + one-line proof + primary "Try a live checkout" (teal) and secondary "Create an account"; right, a live-looking payment record panel (amount figure, lifecycle rail, debit/credit postings), labelled as an example. Dashboard: left sidebar nav, page header with title and primary action, then the work table. Payment detail: amount figure and status header, lifecycle timeline left, details and actions right.

FORM: canon (category standard), chosen by the user over the roll; seed: 479efa9f2e0a939a. Signature move: statement-style money figures (dollars dominant, cents subordinate) and the payment lifecycle rail with pinned ledger postings.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
