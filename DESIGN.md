---
name: Cleartill
description: A calm, precise payments product where money is the loudest thing on every screen.
colors:
  ground: "#f6f7f6"
  surface: "#ffffff"
  surface-rail: "#f0f2f1"
  sunken: "#f3f5f4"
  ink: "#14201c"
  ink-2: "#33403b"
  muted: "#5d6965"
  faint: "#8b9590"
  line: "#e2e6e4"
  line-strong: "#cdd4d1"
  till-teal: "#0d6b63"
  till-teal-hover: "#0a5952"
  till-teal-active: "#084a44"
  till-teal-weak: "#e3f0ee"
  till-teal-ink: "#08524c"
  success: "#1d7a4e"
  success-bg: "#e5f3eb"
  warning: "#8f5600"
  warning-bg: "#fcefd6"
  danger: "#b0281f"
  danger-bg: "#fbe8e6"
  info: "#2c5cb0"
  info-bg: "#e7edf9"
  neutral: "#4c5853"
  neutral-bg: "#eceff0"
  test-mode-bg: "#fff5dc"
  test-mode-ink: "#6a4700"
  code-ground: "#13201c"
typography:
  display:
    fontFamily: "Schibsted Grotesk Variable, Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "52px"
    fontWeight: 700
    lineHeight: 1.04
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Schibsted Grotesk Variable, Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.03em"
  money-display:
    fontFamily: "Schibsted Grotesk Variable, Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "32px"
    fontWeight: 650
    lineHeight: 1.1
    letterSpacing: "-0.025em"
    fontFeature: "\"pnum\", \"lnum\""
  title:
    fontFamily: "Schibsted Grotesk Variable, Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  title-sm:
    fontFamily: "Schibsted Grotesk Variable, Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.015em"
  body-lg:
    fontFamily: "Inter Variable, Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: "Inter Variable, Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"cv11\", \"ss01\""
  body-sm:
    fontFamily: "Inter Variable, Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Inter Variable, Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1
  mono:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    letterSpacing: "-0.01em"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  pill: "999px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
  "10": "40px"
  "14": "56px"
  "22": "88px"
components:
  button-primary:
    backgroundColor: "{colors.till-teal}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "34px"
  button-primary-hover:
    backgroundColor: "{colors.till-teal-hover}"
  button-primary-active:
    backgroundColor: "{colors.till-teal-active}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "34px"
  button-secondary-hover:
    backgroundColor: "{colors.sunken}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "34px"
  button-ghost-hover:
    backgroundColor: "{colors.neutral-bg}"
    textColor: "{colors.ink}"
  button-sm:
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "28px"
  button-lg:
    rounded: "10px"
    padding: "0 20px"
    height: "44px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "38px"
  badge-success:
    backgroundColor: "{colors.success-bg}"
    textColor: "{colors.success}"
    rounded: "{rounded.pill}"
    padding: "0 8px 0 7px"
    height: "22px"
  badge-warning:
    backgroundColor: "{colors.warning-bg}"
    textColor: "{colors.warning}"
    rounded: "{rounded.pill}"
    padding: "0 8px 0 7px"
    height: "22px"
  badge-danger:
    backgroundColor: "{colors.danger-bg}"
    textColor: "{colors.danger}"
    rounded: "{rounded.pill}"
    padding: "0 8px 0 7px"
    height: "22px"
  badge-info:
    backgroundColor: "{colors.info-bg}"
    textColor: "{colors.info}"
    rounded: "{rounded.pill}"
    padding: "0 8px 0 7px"
    height: "22px"
  badge-neutral:
    backgroundColor: "{colors.neutral-bg}"
    textColor: "{colors.neutral}"
    rounded: "{rounded.pill}"
    padding: "0 8px 0 7px"
    height: "22px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "20px"
  nav-item:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.md}"
    padding: "0 10px"
    height: "34px"
  nav-item-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
  table-header:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    padding: "0 16px"
    height: "38px"
  test-mode-banner:
    backgroundColor: "{colors.test-mode-bg}"
    textColor: "{colors.test-mode-ink}"
    padding: "6px 16px"
    height: "32px"
---

# Design System: Cleartill

## Overview

**Creative North Star: "The Clear Statement"**

Cleartill is the category standard for a payments product, executed at full craft: a light, faintly green-grey ground, white panels with hairline borders, one teal that only ever means "you can act here", and money set like a bank statement. The amount is the loudest thing on every screen. Everything else (labels, ids, timestamps, chrome) steps back into muted ink so the figure and its state read instantly.

The system is conventional on purpose. Density is calm rather than packed: 14px body on a 4/8px rhythm, 48px table rows, 20px panel padding, generous page margins (32px 40px on the dashboard, 88px section bands on the landing). Depth comes from soft, low offset shadows on panels and a single lifted shadow on the showcase record. The machinery (lifecycle rail, ledger postings, delivery logs, ids in mono) is shown plainly, as a feature, not hidden behind summaries.

It must never read as Stripe (no gradient hero, no purple, no borrowed marks) and it rejects the generic SaaS template: no glassy cards, no icon-tile feature rows. It runs under a strict CSP, so every asset is a same-origin file.

**Key Characteristics:**
- Light green-grey neutrals; white panels on a #f6f7f6 ground with 1px hairlines.
- One teal (#0d6b63) for actions, selection and focus.
- Statement money: dollars dominant, cents subordinate, tabular figures in tables.
- Status as glyph plus word in soft tinted pills.
- Schibsted Grotesk headings over Inter UI text, JetBrains Mono for ids and code.
- Payment lifecycle rail with ledger postings pinned under each step.
- Test mode always visible in an amber banner on every page.

## Colors

A quiet, slightly green-tinted neutral range carrying one teal accent and five tinted status pairs.

### Primary
- **Till Teal** (`till-teal`): primary buttons, links, focus outline and focus ring (`0 0 0 3px` at 24% teal), caret, text selection tint (18%), the active nav item's icon, and the logo mark. Hover deepens to `till-teal-hover`, press to `till-teal-active`.
- **Teal Wash / Teal Ink** (`till-teal-weak`, `till-teal-ink`): the informational notice (one-time secrets, confirmations) and the empty-state icon tile.

### Neutral
- **Ledger Ground** (`ground`): page background behind everything.
- **Panel White** (`surface`): panels, inputs, auth form side, hosted checkout pay column, active nav pill.
- **Rail Grey** (`surface-rail`): dashboard sidebar ground, checkout summary column, auth aside, landing footer.
- **Sunken** (`sunken`): table headers, ledger headers, key boxes, input affixes, the 3DS bank frame, expanded attempt rows.
- **Ink / Ink 2** (`ink`, `ink-2`): headings and figures in Ink; table cells, nav text and secondary copy in Ink 2.
- **Muted / Faint** (`muted`, `faint`): labels, hints, timestamps, ids, cents, currency codes.
- **Hairline / Strong Hairline** (`line`, `line-strong`): panel borders and dividers; input and secondary-button borders, the lifecycle rail line.
- **Code Ground** (`code-ground`): the dark code block on the landing, and Ink itself grounds the closing CTA band.

### Status
- **Success, Warning, Danger, Info, Neutral** (each with a `-bg` tint): badges, timeline dots, status icons, alerts (danger) and warning notices. Never used decoratively.
- **Test-mode Amber** (`test-mode-bg`, `test-mode-ink`, border `#f0dfae`): the persistent banner only.

### Named Rules
**The One Teal Rule.** Teal is reserved for actions, selection and focus (plus the logo mark and teal-wash notices). It never colours a heading, a figure, a chart fill or a decorative surface.

**The Glyph-and-Word Rule.** Status is never colour alone: every badge and timeline dot carries a lucide glyph (13–14px, stroke 2.25) and a word.

## Typography

**Display Font:** Schibsted Grotesk Variable (with Inter Variable, system-ui)
**Body Font:** Inter Variable (with system-ui; `cv11`, `ss01` enabled)
**Label/Mono Font:** JetBrains Mono Variable (with ui-monospace, SF Mono, Menlo)

**Character:** A firm, slightly condensed grotesk for headings and big figures against a neutral, highly legible UI sans; mono marks anything machine-shaped (ids, event types, card numbers, code). All three are self-hosted through fontsource and ship as files.

### Hierarchy
- **Display** (700, 52px, 1.04, -0.035em): landing hero headline only; 40px under 960px, 34px under 820px.
- **Headline** (700, 34px, 1.12, -0.03em): landing section heads; 26px under 820px. Closing band uses 28px.
- **Money Display** (650, 32px, 1.1, -0.025em): the statement figure on payment detail, balance summary, checkout total and the example record.
- **Title** (650, 24px, 1.25): page h1 in the dashboard; auth card 26px, centered cards 22px, checkout pay 20px.
- **Title Small** (650, 16px, 1.35): panel heads (h2); h3 is 14px.
- **Body Large** (400, 17px, 1.6): hero lede, max 46ch; section intros at 16px.
- **Body** (400, 14px, 1.5): default UI text; tables and nav at 13.5px.
- **Label** (550–600, 12–13px): field labels (13px/550), table headers (12px/600), badges (12px/550), sidebar group labels (11.5px/600, sentence case).
- **Mono** (400, 12.5px, -0.01em): code and `.mono`; ids at 12px in Muted.

### Named Rules
**The Statement Figure Rule.** Money renders through the Money component: the major part (symbol and dollars) at full size in Ink, the decimal and cents in Muted (and at 0.6em, weight 600, in the display size), an optional uppercase currency code at 0.6em raised 0.35em. The element's text content stays the plain formatted amount ("$48.00") so copy, search and screen readers read it normally.

**The Tabular Rule.** Figures in tables, ledgers, inputs and timestamps use tabular numerals and right-align in numeric columns; only the display figure switches to proportional lining numerals.

## Layout

Three shells share one stylesheet:
- **Dashboard:** a 236px sticky sidebar on Rail Grey (painted full height by a background gradient on the grid, with a 1px hairline edge) and a main column padded 32px 40px 64px, content capped at 1120px. Page head: title left, actions right, 24px below. Payment detail is a two-column grid (fluid + 340px, 24px gap).
- **Split pages:** auth and hosted checkout are two equal columns (form on white, context on Rail Grey); inner content capped at 380px (auth) and 420px (checkout).
- **Landing:** 1200px container with 32px gutters, a 60px sticky translucent nav, a two-column hero (88px top), and sections separated by a hairline with 88px padding.

Centered cards (3DS, demo thanks, keys shown once) sit at max 440px.

Spacing follows a 4px base with 8px as the working step (4, 8, 12, 16, 20, 24, 32, 40, 56, 88). Breakpoints: 960px collapses all two-column grids to one and hides the auth aside; 820px turns the sidebar into a wrapping top bar and drops gutters to 16px; 640px turns ledgers and data tables into stacked label/value rows. Nothing scrolls horizontally except a table-wrap.

## Elevation & Depth

A light hybrid: hairline borders define every surface, and soft, low-contrast offset shadows tinted with Ink give panels a slight lift. Shadows are ambient, never hard-edged.

### Shadow Vocabulary
- **Rest** (`box-shadow: 0 1px 2px rgb(20 32 28 / 6%), 0 1px 1px rgb(20 32 28 / 4%)`): panels, secondary buttons, the active nav pill, proof cards. Primary buttons add an inset 1px white highlight at 12%.
- **Raised** (`box-shadow: 0 10px 28px -12px rgb(20 32 28 / 22%), 0 2px 6px rgb(20 32 28 / 6%)`): centered cards (3DS, confirmation).
- **Showcase** (`box-shadow: 0 24px 60px -24px rgb(20 32 28 / 30%), 0 4px 12px rgb(20 32 28 / 7%)`): the landing example record only.
- **Focus** (`box-shadow: 0 0 0 3px rgb(13 107 99 / 24%)`): buttons and inputs on keyboard focus.

### Named Rules
**The Hairline-First Rule.** Every container has a 1px Hairline border; the shadow only softens it. Shadows never replace borders.

## Shapes

Gently rounded and consistent: 8px for controls and inner boxes, 12px for panels and tables, 16px for showcase and centered cards, 18px for the landing CTA band, full pills for badges. Small details (brand tag, skeleton bars) take 4px; small buttons 6px; large buttons 10px. Avatars and timeline dots are circles. The logo mark is a 6.5-radius rounded square with three till-drawer slots.

## Components

### Buttons
Precise and quiet.
- **Shape:** 8px radius, 34px tall, 13.5px/550 text, 7px icon gap.
- **Primary:** Till Teal with white text, rest shadow plus inset highlight. One per view region.
- **Secondary:** white with Strong Hairline border and rest shadow; hover to Sunken.
- **Ghost:** transparent, Ink 2; hover to Neutral wash.
- **Sizes:** small 28px (6px radius, 12.5px), large 44px (10px radius, 15px), block for full width.
- **States:** 150ms ease-out colour transitions; focus shows the teal ring; disabled at 55% opacity; busy buttons show a 14px spinner. On the dark CTA band, primary inverts to white on Ink and secondary to a translucent white outline.

### Badges
- **Style:** 22px pill, tinted status background with matching text, 13px glyph then word. Payment statuses use merchant words ("Uncaptured", "Needs authentication"); delivery, payout and key states keep the API word, capitalized by CSS.

### Cards / Containers
- **Corner Style:** 12px (panels), 16px (record, centered card).
- **Background:** Panel White.
- **Shadow Strategy:** Rest (see Elevation).
- **Border:** 1px Hairline.
- **Internal Padding:** head 12px 20px (min 52px, hairline below), body 20px.

### Inputs / Fields
- **Style:** 38px, white, Strong Hairline border, 8px radius, faint inset shadow, tabular figures; label above at 13px/550 with a 6px gap and optional muted hint.
- **Focus:** border to Till Teal plus the focus ring; input groups take focus-within the same way, with a Sunken affix.
- **Error / Disabled:** `aria-invalid` turns the border Danger; errors appear in a Danger alert with `role="alert"`.

### Navigation
- **Sidebar:** 34px items, Ink 2 at 13.5px/500 with a Muted 16px icon; hover a 5% Ink wash; active is a white pill with rest shadow, 600 weight and a teal icon. Group labels in sentence case. Account block and log out pinned at the foot. Under 820px it becomes a wrapping top bar.
- **Landing nav:** sticky, 86% Ground with blur, hairline below; links hide under 820px.
- **Breadcrumb:** muted back-link, slash, current page as a 13px/600 Inter h1.

### Tables
Header row Sunken at 38px with 12px/600 Muted labels, body rows 48px with hairline dividers and a near-white hover; numeric columns right-aligned. Ids in 12px mono. Below 640px rows stack into label/value cards.

### Payment Lifecycle Rail (signature)
A vertical timeline: 28px circular dots tinted by tone with a glyph, joined by a 1px Strong Hairline; a pending step is a dashed outline dot. Each step shows a title, tabular timestamp, mono event names, and the ledger postings behind it in a bordered 8px-radius mini-table (Account / Debit / Credit, Sunken head). Under 640px postings collapse to rows prefixed "Dr" / "Cr".

### Test-mode Banner
32px amber strip with a flask glyph on every page: "Test mode: simulated payments, never enter a real card."

## Do's and Don'ts

### Do:
- **Do** render every amount with the Money component so dollars lead, cents recede and the text content stays the plain formatted amount.
- **Do** keep Till Teal (#0d6b63) for actions, selection and focus only.
- **Do** pair every status colour with a glyph and a word.
- **Do** give every surface a 1px Hairline border and, at most, the Rest shadow; reserve Showcase for the landing record.
- **Do** ship every font and image as a same-origin file: fontsource packages, `assetsInlineLimit: 0`, no inline `style` attributes or scripts.
- **Do** keep the accessible names the e2e tests pin (labels, buttons, headings, nav links, `role="alert"`, `role="status"`) when restyling.
- **Do** keep the test-mode banner on every page.
- **Do** set ids, event types, card numbers and code in JetBrains Mono.

### Don't:
- **Don't** use teal for headings, figures, illustration or decorative fills.
- **Don't** show a state by colour alone.
- **Don't** split an amount into separate text nodes that read differently to a screen reader, or replace it with an image.
- **Don't** load fonts, scripts or images from a CDN, use `data:` URIs, or write inline styles; the CSP blocks them.
- **Don't** borrow Stripe's look, purple, gradients or marks.
- **Don't** use gradient heroes, glassy cards or icon-tile feature rows.
- **Don't** use hard, zero-blur offset shadows.
