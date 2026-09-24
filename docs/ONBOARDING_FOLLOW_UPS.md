# Onboarding Follow-Ups

## Priority: Simplify Client Onboarding

Status: queued

- Add a non-interactive client profile format containing only business metadata and enabled platforms.
- Reuse agency-managed Google OAuth credentials for Analytics, Search Console, and GBP instead of prompting for them per platform.
- Prompt only for client-specific IDs and secrets that cannot be centrally managed.
- Validate IDs and access before execute; present one concise dry-run summary with readiness and missing keys.
- Ensure blank platform inputs never produce credential-secret writes.
- Replace the interactive credential-heavy flow with the runbook-guided workflow in `docs/META_CLIENT_ONBOARDING_RUNBOOK.md`.

## Mariachi El Cuis: Meta Launch Planning

Status: blocked on Meta new-business partner assignment

- Revisit agency partner assignment when Meta lifts the restriction for this new client Business Portfolio.
- Create the agency System User and store a read-only API token in AWS Secrets Manager after asset access is available.
- Confirm the saved manual campaign draft before publish; require explicit approval before publication or spend.