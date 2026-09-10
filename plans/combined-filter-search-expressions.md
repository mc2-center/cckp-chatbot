# Support multiple independent search terms in `buildExploreUrl` (AND'd filter chips)

## Context

The user asked whether a redirect URL can combine several distinct filter terms — e.g. "glioma" AND "single cell RNA sequencing" as two separate, independently-removable filter chips — rather than one merged phrase "glioma single cell RNA sequencing" (which is all `buildExploreUrl` currently supports: a single `searchExpression` string produces a single `TextMatchesQueryFilter` entry in `additionalFilters`).

Live-tested both shapes directly against staging (`staging.cancercomplexity.synapse.org/Explore/Publications/`) to confirm the portal actually supports this before proposing any change:

- **One combined string** (`additionalFilters: [{searchExpression: "glioma single cell RNA sequencing"}]`): loads correctly, one filter chip reading "glioma single cell RNA sequencing", 607 results.
- **Two independent entries** (`additionalFilters: [{searchExpression: "glioma"}, {searchExpression: "single cell RNA sequencing"}]`): loads correctly, **two separate filter chips** ("glioma" and "single cell RNA sequencing"), 607 results.

Confirmed: `additionalFilters` is a real array the portal iterates over — it's not limited to one `TextMatchesQueryFilter` entry. This means `buildExploreUrl` can be extended to accept more than one independent search term without any portal-side limitation getting in the way.

## Approach

### 1. Rename `searchExpression` → `searchExpressions` (array of strings)

Rather than overloading the existing string parameter to also accept an array (ambiguous for the model to reason about, and Bedrock action-group parameters are typed, so a dual-type field is awkward to declare), rename to a plural array parameter. This is an internal-only interface (no external callers besides this agent's own model), so a clean rename is safe and clearer than maintaining two accepted shapes.

- `agents/cckp-copilot/lambda/cckpSqlRag/lambda_function.py` — `build_explore_url`: replace the current
  ```python
  search_expression = params.get("searchExpression")
  if search_expression:
      query["additionalFilters"] = [{...one filter...}]
  ```
  with a loop over `params.get("searchExpressions")` (a list), appending one `TextMatchesQueryFilter` dict per entry to `additionalFilters`. Keep `facets` exactly as-is (already a list, already produces one `selectedFacets` entry per facet, unaffected by this change — filters and facets already coexist in the same `additionalFilters`/`selectedFacets` arrays).
- Validate: reject non-list input and empty strings within the list with a clear `{"error": ...}` (matching the existing validation style for `facets`).

### 2. Update both OpenAPI schema copies

`agents/cckp-copilot/lambda/cckpSqlRag/openapi.yaml` and the embedded `ApiSchema.Payload` in `agents/cckp-copilot/cloudformation.sql.yaml`:

- `BuildExploreUrlRequest.searchExpression` (string) → `searchExpressions` (`type: array, items: {type: string}`), description updated to explain each entry becomes its own independent, AND'd filter chip.
- Update the `freeTextSearch` example to show a list.

### 3. Update the agent `Instruction` block

`agents/cckp-copilot/cloudformation.sql.yaml`:

- Toolset bullet: `buildExploreUrl(table, searchExpression?, facets?)` → `buildExploreUrl(table, searchExpressions?, facets?)`, with a short note that multiple entries combine as independent AND'd filters (e.g. `["glioma", "single cell RNA sequencing"]` for two separate chips) rather than one merged phrase.
- Worked example: update to reflect the new parameter name (keep it to a single term unless a second worked example is warranted — avoid bloating the already-size-constrained `Instruction`).
- Re-check the `Instruction` character count after editing (last confirmed at 11,559 chars, well under the ~20,000 limit hit earlier this project) and note the new total.

### 4. Tests

`agents/cckp-copilot/lambda/cckpSqlRag/tests/test_lambda_function.py`:

- Update existing `TestBuildExploreUrl` cases that pass `searchExpression` (singular) to the new `searchExpressions` (list) shape.
- Add a case with 2+ entries, asserting `additionalFilters` contains one dict per entry, each with the right `searchExpression` value and `NATURAL_LANGUAGE` mode.
- Add a validation case for non-list input.
- The existing self-verification round-trip check (Addendum 4) already covers this path structurally — no change needed there, since it just re-decodes whatever `query` dict was built.

## Verification

- Run the full Lambda test suite (`python3 -m pytest tests/ -v`), expect all passing including the updated/new cases.
- Re-parse `cloudformation.sql.yaml` with the CFN-aware YAML loader used throughout this project and re-check the `Instruction` length.
- Live-test one real agent-generated multi-term URL against staging (same method used above) to confirm the end-to-end path — not just the two manually-constructed comparison URLs already tested.

## Process

- Store this plan at `plans/combined-filter-search-expressions.md` (this file) for review before implementing.
- After implementing, append an Implementation Report noting changes applied, deviations, and verification results, consistent with this project's established practice for changes to `cloudformation.sql.yaml`/the Lambda.

## Implementation Report

All four approach steps applied as planned, no deviations:

1. **`lambda_function.py`**: `build_explore_url` now reads `params.get("searchExpressions")` (a list) instead of a single `searchExpression` string, building one independent `TextMatchesQueryFilter` dict per entry and appending each to `additionalFilters`. Added validation: non-list input → `{"error": "searchExpressions must be a list of strings"}`; a non-string/empty entry → `{"error": "each searchExpressions entry must be a non-empty string"}`. Updated the function's docstring to document the AND'd-chips behavior, citing the live confirmation below. `facets` was untouched — it was already list-shaped and already coexists with `additionalFilters` in the same query object.
2. **Both OpenAPI schema copies** (`agents/cckp-copilot/lambda/cckpSqlRag/openapi.yaml` and the embedded `ApiSchema.Payload` in `cloudformation.sql.yaml`): `BuildExploreUrlRequest.searchExpression` (string) → `searchExpressions` (array of strings), with an updated description and a new `combinedFreeTextSearch` example alongside the existing single-term one.
3. **Agent `Instruction`**: toolset bullet for `buildExploreUrl` updated to `buildExploreUrl(table, searchExpressions?, facets?)` with a note on independent AND'd chips vs. one merged phrase. Re-parsed the template with the project's CFN-aware YAML loader afterward: `Instruction` is now 11,835 chars (up from 11,559 before this change) and the embedded `ApiSchema.Payload` still parses as valid OpenAPI with `BuildExploreUrlRequest` correctly exposing `table`/`searchExpressions`/`facets`. Both comfortably under the ~20,000-char limit that caused a deployment error earlier in this project.
4. **Tests**: renamed the existing single-term test to the new list shape, added `test_multiple_search_expressions_become_independent_filters` (asserts `additionalFilters` contains one correctly-shaped dict per entry, in order), `test_search_expressions_must_be_a_list`, and `test_search_expressions_rejects_empty_entry`. Updated the two other tests that referenced the old singular parameter (`test_self_verification_passes_for_real_output`, `test_via_lambda_handler`). Full suite: **82/82 passing** (79 prior + 3 new).

### Verification

- `python3 -m pytest tests/ -v` — 82/82 passing.
- Re-parsed `cloudformation.sql.yaml` with the standard `CfnLoader` — both the `Instruction` block and the embedded `ApiSchema.Payload` load cleanly.
- End-to-end live check: called the actual (post-change) `build_explore_url({"table": "publications", "searchExpressions": ["glioma", "single cell RNA sequencing"]})` in a Python shell, took its real returned `url` verbatim, and hard-navigated to it on staging — **"607 results filtered by glioma, single cell RNA sequencing"** with two separate, independently-removable chips, confirming the full path (Lambda code → real encoding → live portal rendering) works as designed, not just the isolated unit tests.

### Correction addendum: the AND-semantics claim in this plan was wrong

A user-reported bug (`plans/enable_and_filtering.md`) and its follow-up (`plans/fix-searchexpressions-and-semantics.md`) found that multiple `searchExpressions` entries do **not** actually AND together — they behave like an OR/union. This plan's verification above never actually distinguished AND from OR: it compared one merged string vs. two separate entries for two terms that plausibly co-occur at a very high rate in this domain (glioma + single-cell RNA-seq), got the identical count (607) either way, and treated that as confirmation of AND semantics. Identical counts are equally consistent with both shapes being OR/union — the test simply wasn't a discriminating one, since the two terms it chose weren't independent enough to tell the difference.

`plans/fix-searchexpressions-and-semantics.md` reran this with a genuinely independent term pair, ground-truthed against known single-term counts (not just "does it match the other shape"), live-tested against **production**: "glioma" alone = 230, "fluorescence microscopy" alone = 1,273, combined = 1,449 — mathematically impossible as an intersection (which can't exceed the smaller set, 230), proving the union/OR behavior directly (230 + 1,273 − 1,449 ≈ 54 overlap, consistent with a union). This held regardless of `searchMode` (`NATURAL_LANGUAGE` vs `BOOLEAN`) or `+`-prefixed required-term operators, tested as both single- and multi-entry `additionalFilters` — all four shapes produced the identical 1,449.

The docstring in `lambda_function.py`, both `openapi.yaml` copies, and the agent `Instruction`'s `buildExploreUrl` bullet have all been corrected to state the real behavior and to default multi-concept filters to `facets` (confirmed to AND correctly) instead. No code behavior changed — `searchExpressions` still builds the same `additionalFilters` shape it did before; only the false "AND'd together" claim was removed from documentation and agent guidance. See `plans/fix-searchexpressions-and-semantics.md` for the full investigation and fix.
