# Fix `buildExploreUrl`'s broken AND semantics for `searchExpressions`

## Context

`plans/enable_and_filtering.md` documents a real, user-observed failure: asked for publications matching glioma **and** fluorescence microscopy, the agent found 6 true matches via `sqlQuery`, built a redirect with `buildExploreUrl({"table": "publications", "searchExpressions": ["glioma", "fluorescence microscopy"]})` per current instructions, and the resulting collection page showed **1,449 results** — three orders of magnitude more than the ground truth. The user caught the discrepancy directly; the agent's own account of what the filter does was wrong.

**Why the current code/instructions believe this already works:** `build_explore_url` (`agents/cckp-copilot/lambda/cckpSqlRag/lambda_function.py:376-389`) turns each `searchExpressions` entry into its own `TextMatchesQueryFilter` with `searchMode: "NATURAL_LANGUAGE"`, appended to `additionalFilters`. The docstring and the agent `Instruction` both assert this renders as independent, AND'd filter chips — "confirmed live against staging," per `plans/combined-filter-search-expressions.md`.

**That confirmation doesn't hold up.** The test in `combined-filter-search-expressions.md` compared one merged string (`"glioma single cell RNA sequencing"`, 607 results) against two separate entries (`"glioma"`, `"single cell RNA sequencing"`, also 607 results) and treated the matching count as proof of AND semantics. It isn't: both queries returned the *same* count, which is exactly what you'd also expect if both are actually OR-ing tokens/entries — a NATURAL_LANGUAGE full-text match is inherently a relevance-ranked, any-term-matches search, not a phrase match, and the two terms tested (glioma + single-cell RNA-seq) very plausibly co-occur in nearly every row that matches either one in this domain. That test never checked against a SQL ground truth, and the two concepts it chose weren't independent enough to distinguish AND from OR. `enable_and_filtering.md`'s two terms (glioma, fluorescence microscopy) are much more independent, and expose the real behavior: the filters are not intersecting.

**A concrete, previously-unexplored lead:** `plans/portal-redirect-url-format-fix.md`, Addendum 5, separately observed — while investigating an unrelated question — that production's own search box produces `qw0` values using `searchMode: "BOOLEAN"` with a proximity operator (`"glioma" @12"`), not `NATURAL_LANGUAGE`. This means the portal's `TextMatchesQueryFilter` supports at least two distinct MySQL-style full-text search modes, and `BOOLEAN` mode is the one that supports explicit required-term (`+word`) and phrase operators in MySQL's own full-text search syntax. This was never tested as a fix for AND semantics — it surfaced as a side observation while closing out a different, unrelated hypothesis.

**Known-good alternative already in hand:** `selectedFacets` (`FacetColumnValuesRequest`) already combines as a true AND — confirmed in `portal-redirect-url-format-fix.md` (`species=Zebrafish` → 19 results, matching live truth) and again in the `enable_and_filtering.md` transcript itself, where rebuilding with facets brought the count down to 3 (much closer to the SQL-confirmed 6, though not identical — likely because facets match an exact controlled-vocabulary bucket while the original `sqlQuery` used a looser `LIKE` match, not because facets are broken). This gap needs to be understood and explained to users, not glossed over.

## Approach

### 1. Run a real discriminating test before changing any code

The existing test methodology (compare two same-domain, likely-co-occurring terms, check the counts match) can't tell AND from OR — fix the test, not just the code, first:

- Pick 2–3 term pairs per table where the true AND count is known via `sqlQuery` (ground truth) and is meaningfully smaller than either term's individual count — i.e., genuinely independent concepts (`enable_and_filtering.md`'s glioma + fluorescence-microscopy pair is a good one to keep; add at least one more pair on Datasets or Tools).
- For each pair, live-test against staging and record the actual result count for:
  a. current behavior: two `additionalFilters` entries, both `searchMode: NATURAL_LANGUAGE` (reproduce the bug first, confirm it's real and repeatable, not a one-off).
  b. two `additionalFilters` entries, both `searchMode: BOOLEAN`, each term prefixed with `+` (e.g. `+fluorescence microscopy` as one filter, `+glioma` as the other) — tests whether BOOLEAN mode alone fixes it, or whether multiple `additionalFilters` entries themselves are OR'd regardless of mode.
  c. a single `additionalFilters` entry, `searchMode: BOOLEAN`, one combined expression with every term `+`-prefixed (e.g. `+glioma +fluorescence +microscopy`) — tests whether AND has to happen *within* one filter rather than *across* filter entries.
- Compare each against the `sqlQuery` ground truth count. Whichever shape (if any) lands closest to ground truth is the one to adopt — don't assume BOOLEAN fixes it without confirming live, the same discipline this project has applied to every other portal-behavior claim.

### 2. If a `searchExpressions`-based shape passes verification — adopt it

- Update `build_explore_url` to build filters that shape (e.g. `searchMode: "BOOLEAN"` with `+`-prefixed terms, single or multiple entries per whatever step 1 showed actually ANDs).
- Decide, based on what actually works, whether `searchExpressions` stays a list of independent concepts (current interface) or needs to change (e.g. a single expression the caller composes with explicit operators) — don't preserve the current interface just for continuity if the working shape needs a different one.
- Update the OpenAPI schemas (both copies) and the agent `Instruction`'s `buildExploreUrl` toolset entry to describe the *verified* behavior, including what operators mean and that untested combinations (e.g. mixing `+required` and bare terms) aren't guaranteed to behave any particular way.

### 3. If no `searchExpressions` shape reliably ANDs — stop claiming it does, and re-scope the tool

- Revert the docstring/`Instruction` claim of AND semantics for multi-entry `searchExpressions`; document instead that multiple entries render as separate, independently-removable chips whose *combined* effect on the result set isn't guaranteed to be a strict intersection (per the confirmed OR-like behavior).
- Add explicit instruction guidance: for any filter request combining ≥2 distinct concepts, prefer `facets` (exact-value, already-confirmed-AND) whenever the concepts map to a facetable column (`tumorType`/`assay`/`tissue`/`species`/`topic`/`operation`/`language`/`theme`/`consortium`); reserve `searchExpressions` for a single free-text concept, or for terms that don't correspond to any known facet column (and in that case, tell the user the result count is an upper bound, not exact).
- Explain the facets-vs-SQL-count gap (3 vs. 6 in the transcript) as a real, expected consequence of exact-bucket matching vs. fuzzy `LIKE` matching — not a new bug — so the agent doesn't need to chase an exact match that facets structurally can't produce.

### 4. Either way: correct what's already committed

- Fix the overclaiming docstring in `lambda_function.py` (`build_explore_url`) and the `Instruction`'s `buildExploreUrl` bullet in `cloudformation.sql.yaml` to match whatever step 1 actually showed — no version of "confirmed live" language should survive unless it's re-earned by this round's testing.
- Append an Addendum to `plans/combined-filter-search-expressions.md` documenting the correction: the original test wasn't a valid AND/OR discriminator, and pointing to this plan for the real finding — consistent with how this project logs corrections against the plan that introduced the original claim (see the addenda pattern in `plans/sql-agent-hosting-vs-indexed-fix.md` and `plans/portal-redirect-url-format-fix.md`).

### 5. Make this class of bug fail loudly next time

`build_explore_url`'s existing self-verification (Addendum 4 in `portal-redirect-url-format-fix.md`) only checks that the `qw0` bytes round-trip — it can't and doesn't check that the portal *interprets* the query as intended. Consider (scope this modestly, don't over-engineer):
- A note in the `Instruction`'s worked example encouraging the agent to sanity-check a multi-filter redirect's implied result count against the `sqlQuery` count it already has in hand when one is available, and say so plainly if they diverge sharply — formalizing the exact recovery behavior the agent already improvised correctly in the `enable_and_filtering.md` transcript, rather than relying on the model to reinvent that judgment call each time.
- This is a mitigation, not a fix for the underlying semantics — it doesn't replace steps 1–4.

## Verification

- Full Lambda test suite (`cd agents/cckp-copilot/lambda/cckpSqlRag && python3 -m pytest tests/ -v`) — update/add `TestBuildExploreUrl` cases for whatever shape is adopted (mode, operator prefixing, single- vs. multi-entry), plus a regression test asserting the *old* NATURAL_LANGUAGE multi-entry shape is no longer what gets produced.
- Live-test the adopted shape against staging for all term pairs from step 1, confirming counts now land at or near the `sqlQuery` ground truth (exact match not required if facets are the adopted path, per the expected-gap explanation above — but the gap must be small and explainable, not another 1,449-vs-6 situation).
- Re-parse `cloudformation.sql.yaml` with the project's CFN-aware YAML loader and re-check the `Instruction` character count against the ~20,000 limit.

## Process

- Store this plan at `plans/fix-searchexpressions-and-semantics.md` (this file) for review before implementing.
- After implementing, append an Implementation Report (changes applied, deviations, live-verification results) here, and add the correction addendum to `plans/combined-filter-search-expressions.md` as described in step 4.
- Not committing unless asked.

## Implementation Report

**Step 1 (discriminating test) — run against production, not staging, per explicit user instruction mid-investigation**, using a helper script (`build_qw0.py` in the session scratchpad) that reproduces `build_explore_url`'s exact encoding to construct test `qw0` URLs directly, live-navigated via browser automation:

| Shape | Config | Result |
|---|---|---|
| Baseline reproduction | `additionalFilters`: 2× `NATURAL_LANGUAGE`, "glioma" + "fluorescence microscopy" | **1,449** (matches the original bug report exactly) |
| Isolate term 1 | `additionalFilters`: 1× `NATURAL_LANGUAGE`, "glioma" only | 230 |
| Isolate term 2 | `additionalFilters`: 1× `NATURAL_LANGUAGE`, "fluorescence microscopy" only | 1,273 |
| Hypothesis: BOOLEAN + `+`-prefix, 2 entries | `additionalFilters`: `["+glioma"]` + `["+fluorescence +microscopy"]`, both `BOOLEAN` | **1,449** (identical to baseline) |
| Hypothesis: BOOLEAN + `+`-prefix, 1 combined entry | `additionalFilters`: 1× `BOOLEAN`, `"+glioma +fluorescence +microscopy"` | **1,449** (identical to baseline) |

230 + 1,273 − 1,449 ≈ 54 overlap — exactly the arithmetic of a **union**, and mathematically impossible as an intersection (which cannot exceed the smaller set, 230). This proves OR/union semantics conclusively, and rules out the plan's BOOLEAN-mode hypothesis: all three `additionalFilters` configurations tested (NATURAL_LANGUAGE, BOOLEAN two-entry, BOOLEAN one-entry) produced the **identical** 1,449, meaning neither `searchMode` nor `+`-prefixed operators are honored for AND purposes on this deployment — the field/operators appear to have no functional effect at all.

**Facets cross-check (also production, also a fresh pair I verified myself, not reused from prior work):** the live filter panel confirmed no "Fluorescence Microscopy" assay value exists at all (only "Cyclic Immunofluorescence," 45 rows) — the original transcript's facet-rebuild example was aspirational, not a value actually present in the controlled vocabulary. Built a real facets test instead: `tumorType=["Glioma"]` (108 alone) + `assay=["Cyclic Immunofluorescence"]` (45 alone) → **1 result**, a real intersection (≤ min(108, 45)), confirming `selectedFacets` combines as a true AND.

**Conclusion: no `searchExpressions`/`additionalFilters` shape achieves AND semantics on this deployment (per the plan's step 3, not step 2).** `facets` is the only mechanism confirmed to AND correctly.

**Step 3 applied — corrected documentation and agent guidance, no functional code change** (since no tested shape improved on the status quo, changing the encoding would have been change for its own sake):

1. **`lambda_function.py`** (`build_explore_url` docstring): replaced the false "confirmed live... AND'd together" claim with the full corrected finding (counts, arithmetic, all four tested shapes, and the facets cross-check), pointing to this plan.
2. **`agents/cckp-copilot/lambda/cckpSqlRag/openapi.yaml`**: corrected the `searchExpressions` field description and the endpoint-level description; replaced the misleading `combinedFreeTextSearch` example (two `searchExpressions` entries, implying a working AND) with a `combinedFacetFilter` example (two `facets` entries, tumorType=Glioma + assay=Cyclic Immunofluorescence) that actually demonstrates a working multi-concept AND.
3. **Embedded `ApiSchema.Payload` in `cloudformation.sql.yaml`**: mirrored both corrections above verbatim (field description + example swap).
4. **Agent `Instruction`'s `buildExploreUrl` toolset bullet**: rewritten to lead with "for any filter combining ≥2 concepts, use `facets`, not `searchExpressions`," state the corrected OR/union finding with the actual numbers, and note multi-entry `searchExpressions` results should be treated as an upper bound. The existing worked example already called `buildExploreUrl` with `facets` (not `searchExpressions`) for its multi-concept redirect — it was already consistent with the corrected guidance and needed no change.
5. **Test file comment**: `test_multiple_search_expressions_become_independent_filters`'s comment corrected to state the shape assertion doesn't imply AND semantics, pointing to the docstring and this plan. No test assertions changed — the actual JSON shape `build_explore_url` produces is unchanged; only the false claim about what it does on the portal was corrected.
6. **`plans/combined-filter-search-expressions.md`**: added a correction addendum (per Approach step 4) explaining why its original AND-confirmation test wasn't actually discriminating.

**Step 5 (sanity-check nudge) — not added.** On reflection this would duplicate guidance already covered by the corrected `buildExploreUrl` bullet ("use facets for ≥2 concepts," "treat multi-entry searchExpressions as an upper bound") — a separate instruction to "cross-check against sqlQuery" would add `Instruction` size for a case the agent should now avoid hitting in the first place (by preferring facets). Deferred as unnecessary rather than implemented speculatively.

### Verification

- Full Lambda test suite: `python3 -m pytest tests/ -v` — **94/94 passing**, no regressions (no test assertions changed, only one comment).
- Re-parsed `cloudformation.sql.yaml` with a CFN-aware YAML loader (custom multi-constructor for `!Sub`/`!Ref`/etc., matching this project's established verification method): the outer template and the embedded `ApiSchema.Payload` (independently re-parsed as OpenAPI) both load cleanly; `BuildExploreUrlRequest` correctly exposes `table`/`searchExpressions`/`facets` with the corrected description text.
- `Instruction` length: **15,149 chars** (up from 14,836 before this fix), still comfortably under the ~20,000-char limit.
- All live counts above were captured against **production** (`cancercomplexity.synapse.org`), per explicit user instruction partway through testing (the investigation started against staging — reproduced the identical 1,449 there too — then switched to production for every subsequent test, which is what's reported in the table above).
