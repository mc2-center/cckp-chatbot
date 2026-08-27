# Fix SQL agent conflating "not indexed in CCKP" with "hosted externally"

## Context

A user reported the CCKP Copilot's SQL agent told them, for a case where at least one dataset *was* actually listed on the portal:

> "I checked the CCKP datasets table against those 2026 publications, but unfortunately the datasets linked to those papers are not yet indexed as CCKP entries — they're stored in external repositories (GEO, SRA, etc.) rather than the CCKP's curated Synapse collection."

This is a real conflation, confirmed against the MC2 Center data model (`/Users/obanks/mc2-center/data-models/kg-pipeline`), not just a phrasing problem:

- **A CCKP Dataset row being "indexed"** is governed only by whether a matching row exists in the `datasets` table (`datasetId`/`datasetName`/`datasetAlias` populated).
- **Where the data is physically hosted** is a separate fact carried by `sourceRepository`, `downloadSynId`, and `externalLink` — three real, confirmed-live columns on that same table (`~/.claude/skills/cckp-search/references/field_reference.md`, "Datasets ... CONFIRMED live columns").
- `schema/mc2_model.linkml.yaml`'s `DatasetView_id` field description (lines 1690–1696) states the identifier "should be equivalent to Dataset Alias" and that alias "**can be the GEO identifier such as GSE12345**" — i.e. externally-hosted-and-indexed-in-CCKP is the **normal, expected** case, not an edge case or a "not yet indexed" state. `downloadSynId` being empty is by design for such rows, not a data gap.
- **Important correction to my own prior fix** (`agents/cckp-copilot/cloudformation.sql.yaml`'s "Resource Detail Page Links" section, added earlier this session): I described the Dataset row's `datasetId` field as "the row's Synapse entity id." That's not always true — per the schema description above, `datasetId` may hold a Synapse id **or** a non-Synapse alias (GEO accession, DOI-like string) depending on hosting. The Detail Page link logic (query by `datasetId` value, whatever it is) stays correct, but the *raw-Synapse-fallback* rule I added — which currently allows falling back to `https://www.synapse.org/Synapse:{id}` and cites "the `datasets` table's own `id`/`downloadSynId` column" as a source of trust — is now imprecise: `downloadSynId` is the only field on this table guaranteed to be a real Synapse id when present; `datasetId` is not. This needs tightening as part of this fix, not left inconsistent.
- The join between Publications and Datasets is also underspecified in the current instructions (`Dataset → Publication via pubMedId` is mentioned once, in passing, with no detail) — per `schema/cckp_portal.linkml.yaml`, the canonical join is `Publication.dataset` → `Dataset.datasetAlias` (`cckp_join` annotation), with `Dataset.pubMedId` (multivalued) as the reverse/denormalized path. If the agent picked a wrong or nonexistent join path, that alone could produce a false "not found" — worth guarding against directly, since it's the same class of failure (confidently reporting absence instead of reporting what was actually checked).

## Approach

Edit only `agents/cckp-copilot/cloudformation.sql.yaml`'s `Instruction` block (no schema/Lambda changes needed — `sourceRepository`, `downloadSynId`, `externalLink`, `dataset`/`datasetAlias`, and `pubMedId` are already plain confirmed-live columns).

1. **Add an explicit "indexed vs. hosted" rule** near the existing dataset-related rules of engagement (~line 286): a CCKP Dataset row existing (found via `sqlQuery`) means the resource **is** indexed in the CCKP, full stop — regardless of `sourceRepository`/`downloadSynId`/`externalLink`. Never say a dataset "isn't indexed," "isn't a CCKP entry," or "hasn't been added to the CCKP" based on where its files are hosted. Report the two facts separately: (a) whether a row was found (indexing), and (b) what `sourceRepository`/`externalLink`/`downloadSynId` say about hosting.
2. **Correct the "known data gap" framing** in the Dataset & File Discovery section (~line 519): a missing `downloadSynId` is expected and correct for externally-hosted datasets, not a gap — check `sourceRepository`/`externalLink` first and explain hosting accurately (e.g. "hosted externally via GEO, accession GSE12345") rather than implying something is missing or broken.
3. **Add real Publication↔Dataset join guidance** (~line 435, the "Join across tables" bullet): document both directions — `publications.dataset` (alias) → `datasets.datasetAlias`, and `datasets.pubMedId` (multivalued) → a publication's `pubMedId` — so the agent has a correct, confirmed path to actually find the linked dataset row before concluding one doesn't exist.
4. **Tighten the raw-Synapse-fallback rule** (added in the prior Detail Page fix, ~line 376–386): restrict it to `downloadSynId` only — remove `datasetId`/`id` as an acceptable source for a `synapse.org` link, since `datasetId` may hold a non-Synapse alias for externally-hosted rows. Add a one-line note explaining why (GEO/DOI-alias case).
5. **Correct the "Resource Detail Page Links" Datasets row wording** (~line 360) and the Dataset & File Discovery section's references to the row's "`id` column" (~line 393, 515, 527, 548): rename to `datasetId` consistently (matching the confirmed live column name — there is no bare `id` column in the documented schema), and soften "the row's Synapse entity id" to something accurate for both hosting cases (e.g. "the row's `datasetId` value — a Synapse id when Synapse-hosted, or the dataset's alias/external accession otherwise").

## Verification

No CCKP agent is deployed yet, so no live invocation test. Instead:

- Confirm `agents/cckp-copilot/cloudformation.sql.yaml` still parses under the CloudFormation-aware YAML loader used for the prior fix in this session.
- Grep the edited block afterward for any remaining "gap"/"not indexed"/"not yet" phrasing not paired with the hosting-vs-indexing distinction.
- Re-read the full "Resource Detail Page Links" + "Dataset & File Discovery" sections end-to-end to confirm `datasetId` vs `downloadSynId` usage is now consistent everywhere (no more bare "id" references left over from the prior fix).

## Process

- Store this plan at `plans/sql-agent-hosting-vs-indexed-fix.md` in the repo, per the established workflow for this file this session.
- After implementing, append an Implementation Report to that file (changes applied, any deviations).
- Not committing unless asked.

## Implementation Report

All five steps applied to `agents/cckp-copilot/cloudformation.sql.yaml`'s `Instruction` block exactly as planned, no deviations.

**Changes applied:**

1. Added a new "Never conflate 'indexed in the CCKP' with 'hosted in Synapse'" rule of engagement (after the existing restriction-checking rule, ~line 287): a `datasets` row existing means the resource is indexed, full stop; hosting location (`sourceRepository`/`downloadSynId`/`externalLink`) is a separate, independently-reportable fact; external hosting on a fully-indexed dataset is normal, not a sign of absence.
2. Rewrote the `getDatasetFiles` `error`-case bullet in "Dataset & File Discovery" (~line 524): a missing/unusable `downloadSynId` now triggers a check of `sourceRepository`/`externalLink` first — if external, the agent reports the hosting repository (and link/DOI if present) rather than relaying a raw error or implying something is missing from the CCKP.
3. Replaced the single passing "Dataset → Publication via `pubMedId`" mention with an explicit "Publication ↔ Dataset join" bullet (~line 440) documenting both real join paths — `publications.dataset` → `datasets.datasetAlias`, and `datasets.pubMedId` (multivalued) → `publications.pubMedId` — with an instruction to try the other direction before concluding no linked dataset exists.
4. Tightened the raw-Synapse-fallback rule (~line 386): removed `datasetId`/`id` as an acceptable source for confirming Synapse-entity-ness before building a `synapse.org` link, leaving only `downloadSynId` and `getDatasetFiles`/`checkRestriction` responses; added an explicit "do not use `datasetId` for this" line explaining why (non-Synapse alias risk for externally-hosted rows).
5. Corrected `datasetId` semantics everywhere it was previously mischaracterized as "the row's Synapse entity id":
   - "Resource Detail Page Links" table's Datasets row (~line 360) now describes `datasetId` as the row's identifying value — a synId when Synapse-hosted, or an alias/GEO-accession/DOI otherwise.
   - "Dataset & File Discovery" step 1 (~line 520) now says "keep the row's `datasetId`" instead of "the row's own `id` column," and also tells the agent to keep `sourceRepository`/`externalLink` for the new error-case handling in step 2.
   - The worked example (~line 532) now uses `datasetId: "syn61795461"` instead of a bare `id:`, with an added parenthetical noting this example happens to be Synapse-hosted (both values are synIds) and that an externally-hosted dataset's `datasetId` could instead be a GEO accession.
   - The final redirect example (~line 553) now says "using the row's `datasetId`" instead of "the row's `id`."

**Verification performed:**

- `agents/cckp-copilot/cloudformation.sql.yaml` re-parses successfully under the same CloudFormation-aware YAML loader used for the prior Detail Page fix.
- Grepped for `"known data gap"`, `"not yet indexed"`, `"isn't indexed"`, `"hasn't been added"` afterward: zero leftover instances of the old framing.
- Grepped for any remaining bare `row's \`id\`` / `id: "syn` references in the edited sections: none — every reference is now `datasetId` (the confirmed live column name) or `downloadSynId`, consistently distinguished throughout.
- Read the full "Resource Detail Page Links," rules-of-engagement, "Join across tables," and "Dataset & File Discovery" sections end-to-end; the new rule, join guidance, and `datasetId`/`downloadSynId` distinction read consistently with each other and with the rest of the file.
- No live CCKP agent is deployed yet, so this could not be tested against a real Bedrock invocation, as anticipated in the plan.

No schema, Lambda, or openapi changes were needed — `sourceRepository`, `downloadSynId`, `externalLink`, `dataset`/`datasetAlias`, and `pubMedId` are all already plain columns returned by the existing `sqlQuery`/`getDatasetFiles` calls; this was entirely a prompt-instruction fix.

### Addendum 1: condensed the whole `Instruction` block (max-size limit)

Unrelated in cause but same file: the user hit the Bedrock Agent's max instruction-size limit (the block had grown to 20,264 chars across this fix, the earlier Detail Page fix, and the `+`-encoding addendum). Rewrote the full `Instruction` block for density without dropping any rule/fact/example: merged the two resource tables (Collection targets + Detail Page links) into one, deduplicated rules stated 2-3x across the file (e.g. "only redirect on explicit request"), replaced three full worked-example XML blocks with one generic skeleton + one concrete example, and merged two overlapping toolset lists into one. Result: 9,333 chars (54% reduction). Verified via YAML re-parse and a grep sweep confirming every load-bearing term/rule from the original survived (all six toolset functions, the join guidance, the indexed-vs-hosted rule, restriction-checking rule, all five Detail Page patterns, etc.) — see chat history for the full before/after checklist. No new plan file was created for this pass since it introduced no new facts/behavior, only restated existing ones more densely.

### Addendum 2: require checking multiple identifying columns before declaring "not indexed"

Live-usage bug, found immediately after Addendum 1: the agent searched a dataset by `datasetId` only, found no match for `GSE247824`, and told the user it "is not indexed in the CCKP" — then, redirected to also check `datasetAlias`, found it immediately ("Yes, GSE247824 is indexed in the CCKP — it matches via the datasetAlias field"). The existing "never conflate indexed vs. hosted" rule only guarded against misreading a *found* row's hosting fields; it didn't require an adequately broad search before concluding "not found" in the first place — the same class of premature-negative-conclusion failure, one step earlier.

Merged a new requirement into that same rule of engagement bullet (kept as one bullet rather than a new one, for size): before declaring a resource "not indexed"/nonexistent, check its other plausible identifying columns, not just one — named explicitly per table using the confirmed live column reference (`~/.claude/skills/cckp-search/references/field_reference.md`): `datasets.datasetId`/`datasetAlias`, `grants.grantId`/`grantNumber`/`grantViewId`, `publications.pubMedId`/`doi`, `educationalresources.title`/`alias`/`internalIdentifier`. Instruction length after this change: 9,793 chars — still well under the pre-condensing size that caused the original max-size error.

No plan-mode round was used for this one (nor Addendum 1) — both are small, tightly-scoped corrections directly continuing this same fix's subject matter, judged low-risk enough (no new unverified facts, only reuse of already-confirmed column names) to apply directly per the user's request, consistent with how the `+`-encoding addendum to the prior Detail Page fix was handled.
