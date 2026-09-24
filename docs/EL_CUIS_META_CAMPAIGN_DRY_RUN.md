# Mariachi El Cuis Meta Campaign Dry Run

Status: live. Campaign was published and activated directly in Meta Ads Manager (client-owned account) outside the MCP — our Meta Ads server has no write access to this account yet (new-business partner-assignment restriction, see `docs/EL_CUIS_ONBOARDING_DRAFT.md`). Final launch budget was reduced from the original dry-run figures below by explicit choice.

## Approved Planning Inputs (superseded — see Actual Launch below)

| Item | Decision |
| --- | --- |
| Total budget | $300 USD |
| Flight length | 15 days |
| Daily cap | $20 USD per day |

## Actual Launch

| Item | Value |
| --- | --- |
| Total budget | $150 USD (deliberately reduced from the $300 plan) |
| Flight length | 14 days (ad set name still reads "15D" — cosmetic mismatch, not corrected) |
| Daily cap | $10 USD per day |
| Live ad set name | `MEC | Leads | LA Local | 15D | $150` |
| Status observed | Active, one ad delivering, one video variant ("Video B - Variant A Reliability ES") still Processing; $0.00 spent as of last check |
| Objective | Leads |
| Primary conversion | `Lead` after a confirmed inquiry/message submission |
| Spanish destination | https://mariachielcuis.com/book |
| English destination | https://mariachielcuis.com/en/book |
| Core claim | El mariachi que contratas es el que llega. Sin sorpresas. |
| Geography | Approximate 20-mile radius centered on ZIP code 90011, subject to final platform targeting review |

## Conversion Events

- `Contact`: one click on `tel:626-922-0091`.
- `Lead`: one successful message or inquiry submission, including `/contact/success`.
- `Purchase`: one completed paid booking/deposit only.

The campaign should optimize for `Lead` when the inquiry confirmation is available. If Lead volume is too low to optimize, use `Contact` temporarily and report Leads separately. Purchase must not fire on a page view, phone click, or message submission.

## Dry-Run Campaign Preview

| Layer | Proposed configuration |
| --- | --- |
| Campaign | `MEC | Leads | LA Local | 15D | $150` (actual, launched) |
| Status | Active — launched directly in Meta Ads Manager, not via MCP |
| Objective | Leads |
| Budget | $10 USD/day, capped at 14 days ($150 total) |
| Geography | Approximate 20-mile radius around ZIP code 90011; review overlap with the named service cities before execution |
| Audience | Broad local audience; do not add narrow interest targeting initially |
| Optimization | `Lead`; fallback to `Contact` only if Lead volume is insufficient |
| Placements | Advantage+ placements, subject to creative format review |
| Spanish ad | Spanish client-approved copy and `https://mariachielcuis.com/book` |
| English ad | English client-approved copy and `https://mariachielcuis.com/en/book` |
| Assets | Client-supplied event photos and vertical videos |

### Draft Spanish Message

Primary text: `El mariachi que contratas es el que llega. Sin sorpresas.`

Headline: `Mariachi para tu celebracion en Los Angeles`

Call to action: `Solicitar informacion`

### Draft English Message

Primary text: `The mariachi you book is the mariachi that arrives. No surprises.`

Headline: `Mariachi for your Los Angeles celebration`

Call to action: `Learn More`

No unverified pricing, availability, guarantees, or service claims may be added without client approval.

## Required Before Campaign Creation

1. Confirm one successful inquiry/message emits `Lead` on each destination used in ads.
2. Confirm the selected event types and approved creative assets.
3. Complete Meta partner/API access when the new-business restriction lifts, or use the client-owned account manually for an approved dry run.
4. Review the paused-campaign dry run and explicitly approve execution.