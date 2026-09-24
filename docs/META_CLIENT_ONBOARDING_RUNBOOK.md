# Meta Client Onboarding Runbook

Use this runbook to prepare a client-owned Meta Ads setup before any campaign is created. It separates client assets from the agency integration and keeps all ad-account writes behind dry-run review and explicit approval.

## Asset Ownership

| Asset | Owner | Purpose |
| --- | --- | --- |
| Business Portfolio, ad account, Page, Instagram account, dataset/pixel | Client | Owns data, billing, tracking, campaigns, and ad spend. |
| Developer app and System User | Agency | Enables API access across authorized client assets. |
| GTM container | Client or authorized website operator | Installs and controls browser tracking. |

Do not create a developer app per client. Use the agency-owned developer app after the client grants the agency access. Do not place any token, app secret, or CAPI token in chat, repository files, or `.env` files that may be shared.

## Intake

Collect and confirm:

- Client display name, business key, backend tenant ID, website, phone, service area, and account owner.
- Total budget, campaign duration, primary objective, and written approval boundary.
- Existing ad account ID, Page, Instagram professional account, and dataset/pixel ID, if they exist.
- Booking or inquiry URLs for each supported language.
- Client-approved creative assets and only claims that can be substantiated.
- The conversion hierarchy: inquiry, qualified lead, booking deposit, and completed sale.

## Client-Owned Meta Assets

1. In the client Business Portfolio, create or confirm one ad account.
   - Record its ID, currency, time zone, billing owner, and administrators.
   - Do not create a second ad account when an existing client-owned account is suitable.
2. Create or confirm a website dataset/pixel.
3. Connect the dataset/pixel to the client ad account.
4. Install GTM on every website page, or use an approved direct Pixel installation if GTM is unavailable.
5. Install the Meta Pixel base tag through GTM.

## Event Design And Verification

Use distinct events for different levels of intent:

| Event | Valid trigger | Must not trigger on |
| --- | --- | --- |
| `PageView` | Base Pixel load | Not applicable |
| `Contact` | One click on the client phone `tel:` link | Address, menu, social, or unrelated links |
| `Lead` | One successful inquiry/message form confirmation, such as a `/contact/success` page | Form starts, generic button clicks, or phone clicks |
| `Purchase` | One completed paid deposit or booking | Page views, contact clicks, form submissions, or confirmation-page reloads |

Before publishing GTM, use Preview and Meta Events Manager Test Events to confirm each event fires exactly once for its intended action. Keep `Purchase` only when it represents a real payment and capture a value plus `USD` currency whenever the booking system supports it.

Publish GTM with a clear version name, for example `Meta Pixel: Contact, Lead, Purchase`.

## Agency Access And API Readiness

1. In the client Business Portfolio, add the agency Business Portfolio as a partner and assign only needed assets.
2. Start with read-performance/read access unless approved campaign management is needed.
3. In the agency Business Portfolio, create an Employee System User and assign the agency developer app.
4. Assign that System User only the authorized client ad account and dataset.
5. Generate a token with `ads_read` for reporting. Add `ads_management` only after campaign-write tooling, dry-run review, and client approval are in place.
6. Store API tokens and app secrets in AWS Secrets Manager at the client platform secret path. Validate read access before enabling Meta Ads in the client manifest.

New Meta Business Portfolios may temporarily prevent partner assignment. Do not bypass that restriction. Keep the client owner as direct administrator, continue tracking and draft planning manually, and return to this section when Meta lifts the restriction.

## Campaign Dry Run

Before a campaign is created, produce a written preview containing:

- Campaign name, paused-on-creation status, total budget, daily cap, and flight dates.
- Objective, conversion event, dataset, URL, language, CTA, geography, age, placements, and exclusions.
- One broad local audience before testing narrow interests.
- Separate ads for separate languages, each pointing to the matching-language destination.
- Approved copy, creative, and a list of claims intentionally excluded.

Use `Lead` when a verified inquiry confirmation exists. Use `Contact` temporarily only if lead volume is too low. Use `Purchase` for optimization only after reliable paid-booking volume exists.

Never publish or activate a campaign from the draft review alone. Obtain explicit approval immediately before each external write.

## Completion Checklist

- [ ] Client manifest and required non-Meta platforms are ready.
- [ ] Client-owned ad account and dataset are connected.
- [ ] Tracking events are live and correctly scoped.
- [ ] GTM container is published.
- [ ] Agency partner/System User/API access is ready, or the access restriction is documented.
- [ ] Campaign dry run has been reviewed.
- [ ] Explicit approval exists for campaign creation and, separately, activation/spend.