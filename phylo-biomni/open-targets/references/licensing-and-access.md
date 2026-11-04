# Licensing and access — Open Targets Platform

This reference separates data license, commercial-use terms, API authentication, observed response
behavior, and residual operational uncertainty. It exists because the original skill overreached its
evidence (OT-10): it inferred an unlimited-use guarantee from missing rate-limit headers.

## Data license

| Item | Value |
|---|---|
| Dataset | Open Targets Platform association and evidence data |
| License | CC0 1.0 Universal (Public Domain Dedication) |
| Source | https://platform-docs.opentargets.org/data-access/datasets |
| Access date | 2026-08-16 |

Open Targets data is released under CC0 1.0. No attribution is legally required, though the project
requests citation of the latest release paper. CC0 places the data in the public domain; commercial
use is permitted without restriction.

## Official commercial-use statement

The Open Targets Platform documentation states that its data is available under CC0 1.0 and that the
API requires no authentication. No separate commercial-use agreement, registration, or payment is
documented for read-only API access. This is a statement about what the documentation says, not a
warranty of service availability or fitness for a particular commercial purpose.

## API authentication

| Item | Value |
|---|---|
| Endpoint | https://api.platform.opentargets.org/api/v4/graphql |
| Authentication | None required (no API key, token, or registration) |
| Protocol | HTTPS POST with JSON body |
| Access date | 2026-08-16 |

The GraphQL API accepts unauthenticated POST requests. No credentials are sent or stored by this
skill's client. The client redacts any variable whose key contains `token`, `key`, `secret`, `auth`,
or `password` before persisting it to the operation ledger.

## Observed response behavior

| Observation | Value |
|---|---|
| HTTP status on success | 200 |
| Body on success | JSON with `data` key; GraphQL `errors` key absent or null |
| Body on field/schema error | HTTP 200 with `errors` array (body-level GraphQL error) |
| Body on transport error | HTTP 4xx/5xx with error body |
| Content-Type | application/json |
| Release observed | 26.06 (dataVersion year=26, month=06) |
| API version observed | 26.6.3 (apiVersion x=26, y=6, z=3) |

The client rejects HTTP 200 responses that carry a GraphQL `errors` array (OT-02). It does not
treat a 200-with-errors as a success.

## Rate limits and service stability — unknown and unpromised

**No rate-limit headers or documented rate limits were observed.** The absence of rate-limit
headers is not evidence of unlimited use. The Open Targets documentation does not publish a
service-level agreement, uptime guarantee, rate limit, or stability commitment for the public
GraphQL API as of the access date.

This skill enforces conservative client-side budgets (max 50 entities, 10 pages, 500 rows, 50 MB,
600 seconds elapsed) precisely because the API's limits are unknown. Do not infer unlimited-use
from missing headers, and do not loop the API per entity for genome-scale work — use the bulk data
download route instead.

## Residual operational uncertainty

1. **Rate limits unknown.** The API may throttle or block unauthenticated clients without warning.
2. **No SLA.** Uptime, latency, and data freshness are not guaranteed.
3. **Schema may change between releases.** The pinned query registry and schema-smoke gate detect
   drift but cannot prevent it; re-validate fixtures after each release.
4. **Release cadence.** Open Targets publishes periodic data releases; rankings and counts change
   with each release. Do not freeze today's ranking as a timeless result.
5. **Bulk data boundary.** Genome-wide or per-entity-at-scale work exceeds the API's intended use;
   hand off to the official bulk parquet download (AWS Open Data / FTP).
