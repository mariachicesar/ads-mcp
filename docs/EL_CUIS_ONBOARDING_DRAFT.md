# Mariachi El Cuis Onboarding Draft

Status: analytics, Search Console, and Google Business Profile onboarding completed and verified live in AWS Secrets Manager. Meta tracking is live and a Meta campaign is active — published directly in Meta Ads Manager (client-owned account), not through the MCP, since our Meta Ads server still has no write access to this account. See `docs/EL_CUIS_META_CAMPAIGN_DRY_RUN.md` for the live campaign state.

## Client

| Field | Value |
| --- | --- |
| Display name | Mariachi El Cuis |
| Business key | `el-cuis` |
| Tenant ID | 16 |
| Industry | Mariachi band / entertainment / music |
| Website | https://mariachielcuis.com/ |
| Phone | 626-922-0091 |

## Service Area

Primary market: Los Angeles, centered on ZIP code 90011. Planning target: local service area within approximately 20 miles, subject to a later geo-targeting plan and explicit approval.

Named markets: Los Angeles, Beverly Hills, Whittier, Downey, Pasadena, La Habra, Altadena, West Hollywood, Hollywood, Santa Monica, and Compton.

## Platform Draft

| Platform | Status | Non-secret metadata | Before activation |
| --- | --- | --- | --- |
| Google Ads | Disabled | No customer or manager account supplied | Confirm campaign strategy, conversion events, account IDs, budget schedule, and geo plan. |
| GA4 | Enabled and ready | Property ID: `554624356` | Verify reporting with a read-only live check after deployment. |
| Search Console | Enabled and ready | Site URL: `https://www.mariachielcuis.com/` | Verify reporting with a read-only live check after deployment. |
| Google Business Profile | Enabled and ready | Account ID: `6251638630856099146`; location ID: `1466894401094560539` | Verify the location with a read-only live check after deployment. |
| Meta Ads | Disabled in MCP | Ad account `944318098176585`; dataset/pixel `1095881046227137`; browser tracking is active | New-business partner restriction prevents agency API access. Resume agency partner and System User setup when Meta permits it. |

## Marketing Constraints

- Objective: conversions.
- Actual launch budget: $150 total Meta Ads campaign budget ($10/day, 14 days) — reduced from the original $300 plan by explicit client/user choice. Campaign is live.
- Proposed geography: local area approximately 20 miles from ZIP code 90011. This is a planning input only; final targeting must account for the named markets and platform targeting rules.
- No further budget changes, geo changes, or platform writes are authorized beyond what has already launched, without a new approval.
- Detailed campaign planning: `docs/EL_CUIS_META_CAMPAIGN_DRY_RUN.md`.
- Reusable process: `docs/META_CLIENT_ONBOARDING_RUNBOOK.md`.

## Activation Checklist

1. Confirm the saved campaign draft matches the final budget, dates, audience, language-specific destinations, creative, and approved claims.
2. Wait for Meta to lift the new-business partner-assignment restriction, then grant agency asset access and configure the agency System User for read-only API access.
3. Review the Meta campaign draft, then explicitly approve publication and spend as a separate write action.
