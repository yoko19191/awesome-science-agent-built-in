# ClinicalTrials.gov API v2 Reference

The live retrieval path uses `scripts/query_clinicaltrials.py` (preserved verbatim from the prior
package). This reference documents the endpoint, parameters, and the fail-closed pagination contract
it enforces. Classification and export are downstream (see `classification.md`, `output-schema.md`).

## Endpoint

```
GET https://clinicaltrials.gov/api/v2/studies
```

Free, no authentication required. Rate limit ~50 requests/minute.

## Query Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `query.cond` | string | Condition/disease search | `"Crohn's Disease" OR "Ulcerative Colitis"` |
| `query.intr` | string | Intervention search | `risankizumab` |
| `query.spons` | string | Sponsor name search | `Takeda` |
| `query.locn` | string | Location search | `United States` |
| `query.term` | string | General search | `anti-IL23 IBD` |
| `filter.overallStatus` | string | Comma-separated status filter | `RECRUITING,ACTIVE_NOT_RECRUITING` |
| `filter.phase` | string | Comma-separated phase filter | `PHASE2,PHASE3` |
| `pageSize` | integer | Results per page (max 1000) | `1000` |
| `pageToken` | string | Pagination token from previous response | |
| `countTotal` | boolean | Include total count in response | `true` |
| `format` | string | Response format | `json` |

## Status Values

| API Value | Meaning |
|-----------|---------|
| `RECRUITING` | Actively recruiting participants |
| `NOT_YET_RECRUITING` | Not yet started recruiting |
| `ACTIVE_NOT_RECRUITING` | Ongoing but not enrolling |
| `ENROLLING_BY_INVITATION` | Enrolling by invitation only |
| `COMPLETED` | Study completed |
| `TERMINATED` | Stopped early |
| `WITHDRAWN` | Withdrawn before enrollment |
| `SUSPENDED` | Temporarily paused |
| `UNKNOWN` | Status cannot be confirmed by the registry |
| `AVAILABLE` | Expanded access is available |
| `NO_LONGER_AVAILABLE` | Expanded access is no longer available |
| `TEMPORARILY_NOT_AVAILABLE` | Expanded access is temporarily unavailable |
| `APPROVED_FOR_MARKETING` | Expanded-access intervention is approved for marketing |
| `WITHHELD` | Expanded-access record is withheld |

The default landscape policy uses all 14 values above. An active-only view is used only when the
user explicitly requests current recruiting/ongoing records or supplies an explicit status list.

## Versioned simple-prompt profile

`assets/query_profiles.json` contains the incident-regression profile `psma-prostate-cancer`. A prompt
that contains both a PSMA/FOLH1 target trigger and a prostate-cancer condition trigger resolves to
that profile without clarification. The profile pins three condition terms, the reviewed 59-alias OR
set, and all 14 statuses. `coverage_scope.json` records its id, version, review date, alias count, and
SHA-256 so a report cannot silently use an agent-reconstructed alias list.

## Phase Values

| API Value | Display |
|-----------|---------|
| `EARLY_PHASE1` | Phase 1 |
| `PHASE1` | Phase 1 |
| `PHASE2` | Phase 2 |
| `PHASE3` | Phase 3 |
| `PHASE4` | Phase 4 |
| `NA` | Not Applicable |

A study can carry multiple phases (e.g., `["PHASE2", "PHASE3"]` → display `Phase 2/3`). `Not
Applicable` is a real, common value (observational and many device/diagnostic studies) and is
reported explicitly, never folded into a phase subtotal.

## Response Structure (fields the helper reads)

```json
{
  "totalCount": 1234,
  "nextPageToken": "...",
  "studies": [
    {
      "protocolSection": {
        "identificationModule": { "nctId": "NCT...", "briefTitle": "...", "officialTitle": "..." },
        "statusModule": { "overallStatus": "RECRUITING", "startDateStruct": {"date": "2023-01"} },
        "sponsorCollaboratorsModule": { "leadSponsor": {"name": "...", "class": "INDUSTRY"} },
        "conditionsModule": { "conditions": ["..."] },
        "armsInterventionsModule": {
          "interventions": [{"type": "DRUG", "name": "...", "description": "..."}]
        },
        "designModule": {
          "studyType": "INTERVENTIONAL",
          "phases": ["PHASE3"],
          "enrollmentInfo": {"count": 500}
        },
        "contactsLocationsModule": { "locations": [{"country": "United States"}] },
        "oversightModule": { "isFdaRegulatedDrug": true },
        "descriptionModule": { "briefSummary": "A study of..." }
      }
    }
  ]
}
```

The intervention `type`/`name`/`description` and `briefSummary` are the record-grounded evidence the
classifier uses when present (raw API path). They are frequently absent from a compiled offline CSV;
their availability is recorded rather than assumed (see `output-schema.md`).

## Sponsor Classes

| Value | Meaning |
|-------|---------|
| `INDUSTRY` | Pharmaceutical/biotech company |
| `NIH` | National Institutes of Health |
| `FED` | Other US Federal agency |
| `OTHER` | Academic, hospital, or other |
| `OTHER_GOV` | Non-US government |
| `NETWORK` | Clinical network |
| `INDIV` | Individual investigator |

## Pagination and the fail-closed contract

Results are paginated via `nextPageToken`. The packaged query helper **fails closed** — it raises and
discards partial results rather than exporting an incomplete set — when:

- an API request fails after an earlier page succeeded,
- `max_pages` is reached while a `nextPageToken` remains,
- `totalCount` changes during pagination, or
- the retrieved record count does not match `totalCount`.

Do not export records from a failed attempt. Increase the safety cap or fix the API failure and rerun
the complete declared query. Only a fully exhausted query yields
`retrieval_state = complete_for_declared_query`, which `export_all` requires before writing anything.

## Rate Limiting

- ~50 requests per minute per IP; use a 1–2 second delay between paginated requests.
- No authentication required.
