# Support both production and staging Detail Page link formats

## Context

The current "Resources: Collection & Detail Page Links" table (`agents/cckp-copilot/cloudformation.sql.yaml`) has one Detail Page format per resource — a plain path segment (e.g. `/Explore/Datasets/{datasetId}`) — confirmed correct against `staging.cancercomplexity.synapse.org` during the redirect-format investigation (`plans/portal-redirect-url-format-fix.md`).

The user has now confirmed: **production** (`cancercomplexity.synapse.org`) still uses the older `DetailsPage?key=value` query-string format for Detail Pages, and confirmed that the table in place *before* that investigation (git commit `1d59f70`, immediately prior to `7b5c0b2`) is correct for production for all 5 resource types — not just Datasets. That table:

| Resource | Detail Page Target (production) |
|---|---|
| Datasets | `/Explore/Datasets/DetailsPage?datasetId={datasetId}` |
| Publications | `/Explore/Publications/DetailsPage?pubMedId={pubMedId}` |
| Tools | `/Explore/Tools/DetailsPage/Details?toolName={toolName}` (spaces → `+`) |
| Grants | `/Explore/Grants/DetailsPage?grantId={grantId}` |
| Educational Resources | `/Explore/Educational%20Resources/DetailsPage?title={title}` (spaces → `+`; note the path segment itself is `%20`-encoded here, unlike the plain-path staging/collection forms) |

Scope, confirmed via `AskUserQuestion`: this applies to the **human-facing `<chat>` markdown link only**. The `<target>` auto-redirect stays on the current staging-confirmed path-segment format — a redirect has to match whatever site actually hosts the live chat session, so it isn't a style choice the way a displayed link is.

## Approach

In `agents/cckp-copilot/cloudformation.sql.yaml`'s Instruction:

1. Add a **Production Detail Page Link** column to the existing table, using the format above verbatim for all 5 resources (git commit `1d59f70` values), alongside the existing (unchanged) Detail Page Target column.
2. Update the rule below the table: for a `<chat>` markdown link, default to the new Production column (absolute — prepend `https://cancercomplexity.synapse.org`); only use the existing Detail Page Target column (absolute — prepend `https://staging.cancercomplexity.synapse.org`) if the user explicitly asks for a staging link. Note explicitly that `<target>` is unaffected and always uses the Detail Page Target column, relative, regardless of this choice.
3. Preserve the format-specific space-encoding difference exactly as each format uses it: `%20` for the staging/redirect column (already established), `+` for the new production column (per the recovered pre-fix table) — don't conflate the two.
4. Update the sentence introducing the table ("Both confirmed live... no query string, no DetailsPage") since it's no longer accurate now that a DetailsPage-style column is back, intentionally, for production.

## Verification

- Re-parse `cloudformation.sql.yaml` with the project's CFN-aware YAML loader; re-check `Instruction` length against the ~20,000-char limit.
- No Lambda/OpenAPI changes — this is an Instruction-only edit.

## Process

- Store this plan at `plans/dual-environment-detail-page-links.md` for review before implementing.
- After implementing, append an Implementation Report.

## Implementation Report

All four approach steps applied as planned, no deviations:

1. Added a **Production Detail Page Link** column to the Resources table, using the recovered pre-fix (commit `1d59f70`) values verbatim for all 5 resources, including the Tools row's `DetailsPage/Details` double-segment and the Educational Resources row's `%20`-encoded path segment.
2. Updated the "Redirect vs. present-to-user" rule: `<target>` is called out as unaffected (always the Detail Page Target column, relative); `<chat>` markdown links now default to the Production column (absolute, `cancercomplexity.synapse.org`), falling back to the Detail Page Target column (absolute, `staging.cancercomplexity.synapse.org`) only when the user explicitly asks for a staging link.
3. Preserved the two formats' different space-encoding conventions distinctly (`%20` for the redirect/staging-link column, `+` for the Production column) rather than merging them into one rule.
4. Replaced the table's stale introductory sentence (which claimed both target types had "no query string, no DetailsPage") with one explaining both Detail Page formats are real, current, and environment-specific rather than one being a bug.

### Verification

- Re-parsed `cloudformation.sql.yaml` with the project's CFN-aware YAML loader: `Instruction` is now 13,106 chars (up from 11,835), comfortably under the ~20,000-char limit. The embedded `ApiSchema.Payload` (untouched by this change) still parses correctly.
- No Lambda or OpenAPI schema changes were needed or made — this was an Instruction-text-only edit.
