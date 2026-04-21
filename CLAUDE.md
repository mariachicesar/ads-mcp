# AI Marketing Team — Project Brief

This file is the source of truth for all agents, MCP servers, and tools built in this project.
Always read this file first before making any changes to campaigns, code, or configurations.

---

## Project Vision

Build a fully autonomous AI marketing team running on AWS, connected via MCP servers. The team covers three functions:

1. Ads Agent — manages Google Ads and Meta Ads campaigns
2. Analytics Agent — monitors GA4 and Search Console
3. Content Agent — creates ad copy, blog posts, landing page content, and social media content

All agents share common AWS infrastructure, AWS Secrets Manager, and a single orchestration model.

---

## Businesses

### RnR Electrician
- Google Ads Account ID: 1057140994
- Industry: Electrician / home services
- Service area: Moving to USC Village, Los Angeles within about 2 weeks. Do not update geo targeting until move is confirmed.
- Target cities after confirmed move: Pasadena, Glendale, Burbank, Silver Lake, Koreatown, Culver City, West Hollywood, Downtown LA, San Marino, North Hollywood
- Budget: $15/day, $10 max CPC cap
- Bidding: Maximize Clicks
- Protected rules:
  - Pasadena-San Marino campaign is intentionally paused and must not be touched
  - NoHo EV Charge ad group is paused and must not be touched
  - Keywords were last set April 2026 and may not be changed without explicit approval
  - No geo changes until USC Village move is explicitly confirmed by the user

### GQ Custom Painting
- Google Ads Account ID: 7586427009
- Website: gilqpaiting.com (intentional typo, never auto-correct)
- Industry: Painting contractor
- Service area: Pasadena, Altadena, Whittier, Downey, broader LA County
- Budget: $500/month across Whittier and Altadena campaigns
- Bidding: Smart Bidding in learning phase
- Services: interior, exterior, cabinet painting are equal priority
- Small Jobs campaign ad groups:
  - Popcorn ceiling removal
  - Fence painting
  - Ceiling painting
  - Drywall repair
  - Door painting
  - Garage door painting
- Protected rules:
  - Do not change campaign structure without explicit user approval
  - Zero impressions during learning phase are normal
  - SEO audit exists already
  - Copywriting for location and service pages has not started yet

---

## Standing Rules

1. Always check this file before suggesting campaign changes.
2. Never reverse a prior recommendation without explicitly flagging the conflict first.
3. Never change RnR geo targeting until the user confirms the USC Village move.
4. Never touch the RnR Pasadena-San Marino campaign.
5. Never change GQ campaign structure without explicit approval.
6. RnR keywords were set April 2026 and require approval before changes.
7. All write operations require user confirmation before execution.
8. Always show a dry-run plan before any write operation.

---

## Architecture

Claude Desktop or other client
-> reverse proxy
-> MCP services on EC2
-> Google Ads / Meta / GA4 / Search Console / Claude API

Infrastructure requirements:
- AWS EC2 Ubuntu 24.04
- AWS Secrets Manager for all credentials
- systemd for long-running services
- Nginx reverse proxy with HTTPS
- Health endpoint per service

---

## Build Order

1. Project brief
2. Google OAuth setup
3. AWS EC2 launch and IAM role
4. EC2 bootstrap
5. Domain and DNS
6. Google Ads MCP server
7. Client integration test
8. Meta Ads MCP server
9. GA4 MCP server
10. Search Console MCP server
11. Content Agent
12. Cross-agent orchestration

---

## Implementation Rules

- Python 3.12
- FastMCP for service layer where applicable
- Official SDKs preferred for all platforms
- No credentials in code or committed environment files
- Every write tool must support a dry-run mode and an execute mode
- Every write tool must enforce business rules before execution
