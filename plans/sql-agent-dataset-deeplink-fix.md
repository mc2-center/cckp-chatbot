# Fix SQL agent's dead-end response for single-resource "deep link" requests

## Context

A user reported the CCKP Copilot's SQL agent gave a confusing, unhelpful reply when asked to navigate to a specific dataset's page:

> "I apologize for the confusion. While I can redirect you to the CCKP Datasets collection page, I don't have a confirmed deep-link format for individual dataset records within the CCKP portal itself."

Tracing this through `agents/cckp-copilot/cloudformation.sql.yaml`'s `Instruction` block (the agent's system prompt), it's not a hallucination — it's the agent correctly following a real gap in its own instructions:

- The "Format most relevant hits as markdown links" guidance (`[Dataset Name 1](url)`, around line 409/418–419) never defines what `url` actually is.
- The instructions explicitly say (lines 344–351): *"Entity Details page redirects (single-resource deep links) are not yet confirmed for CCKP — only offer Collection-page redirects for now."*
- The prompt's own worked example offers the guideprompt **"Take me to the dataset's page"** (line 478) — inviting exactly the request the instructions say can't be fulfilled, with no defined fallback beyond an apology.
- The same vagueness shows up at line 286 ("point the user to the resource's page" for restricted resources).

**The premise was actually wrong, not just underspecified.** The user confirmed real, working per-resource DetailsPage URL patterns exist for all five resource types:

| Resource | Confirmed URL pattern | Key field |
|---|---|---|
| Datasets | `https://cancercomplexity.synapse.org/Explore/Datasets/DetailsPage?datasetId={datasetId}` | `datasetId` = the row's Synapse entity id (e.g. `syn61795461`) |
| Publications | `https://cancercomplexity.synapse.org/Explore/Publications/DetailsPage?pubMedId={pubMedId}` | `pubMedId` = the row's `pubMedId` column (e.g. `42151118`) |
| Tools | `https://cancercomplexity.synapse.org/Explore/Tools/DetailsPage/Details?toolName={toolName}` | `toolName` = the row's tool-name field (confirm exact deployed column via `getColumns`) |
| Grants | `https://cancercomplexity.synapse.org/Explore/Grants/DetailsPage?grantId={grantId}` | `grantId` = the row's grant-id field (confirm exact deployed column via `getColumns` — distinct from the `grantNumber` join key already referenced elsewhere in the prompt) |
| Educational Resources | `https://cancercomplexity.synapse.org/Explore/Educational%20Resources/DetailsPage?title={title}` | `title` = the row's title field |

Per explicit user direction: **prioritize these direct portal links** for all five types. Only fall back to a raw Synapse entity URL (`https://www.synapse.org/Synapse:{id}`) when the entity being linked is a Dataset itself or a Synapse Dataset Collection (e.g. a Folder/Project container returned by `getDatasetFiles`) — never for Publications, Tools, Grants, or Educational Resources, and never as the *first* choice for a Dataset when the portal DetailsPage link applies.

## Approach

Edit only `agents/cckp-copilot/cloudformation.sql.yaml`'s `Instruction` block (no Lambda/openapi changes needed — every key field above is already a plain column returned by `sqlQuery`'s `SELECT *`).

1. **Add a "Resource Detail Page Links" reference table** (near the existing "Valid Tables & Targets for Redirect" table, ~line 334–351) listing all five confirmed DetailsPage patterns above, replacing the current "not yet confirmed" language. Note explicitly: confirm exact deployed column names for `toolName`/`grantId`/`title` via `getColumns` before relying on them, the same way the prompt already tells the agent to do for unfamiliar tables (line 368).
2. **Define `url` in the markdown-link guidance** (~line 409): instruct the agent to build each listed resource's link from the matching DetailsPage pattern above using that row's key field — this is what makes `[Dataset Name 1](url)` concrete. This makes every resource listed in `<chat>` immediately clickable without requiring a redirect action.
3. **Add the raw-Synapse fallback rule**, scoped exactly as directed: only for a Dataset entity itself or a Dataset Collection/container (e.g. from `getDatasetFiles`'s `"container"` case, which has no portal-level record of its own) — never for the other four resource types, and never in preference to the portal link when one applies. Critically, require the agent to **confirm the id is actually a Synapse entity of that kind before linking to it** — e.g. it came from the `datasets` table's own `id`/`downloadSynId`, or from a `getDatasetFiles`/`checkRestriction` response (which only ever return real Synapse entity ids) — rather than assuming any arbitrary id qualifies. Never construct a `synapse.org` link from an unverified or guessed id.
4. **Update the two worked examples** (~line 407–447, ~line 461–480) so "Take me to the dataset's page" resolves to a concrete `datasetId`-keyed portal link (either inline in `<chat>`, or via the existing `<actions><redirect>` mechanism with `<target>/Explore/Datasets/DetailsPage?datasetId={id}</target>`) instead of the current apology.
5. **Update the restricted-access line** (286) to point at the same resource-type-appropriate DetailsPage link instead of the vague "resource's page."

## Verification

No CCKP agent is deployed yet (per `agents/README.md`), so this can't be tested against a live Bedrock invocation. Verification instead:

- Read through the full edited `Instruction` block afterward and confirm every resource type has a concrete, correctly-scoped link rule (no leftover "not confirmed" language without a stated alternative), and that the Synapse-fallback rule is scoped only to Datasets/Dataset Collections as directed.
- Confirm `agents/cckp-copilot/cloudformation.sql.yaml` still parses as valid YAML after edits (`python3 -c "import yaml; yaml.safe_load(open('agents/cckp-copilot/cloudformation.sql.yaml'))"`).
- Manually check the rewritten example flows for internal consistency (SQL/query syntax matches conventions used elsewhere in the same file; redirect XML matches the `<actions>` structure already defined).

## Process

- Plan stored at `plans/sql-agent-dataset-deeplink-fix.md` in the repo (new top-level `plans/` directory) for review, per the user's requested workflow.
- After implementing, append an "Implementation Report" section to that same file describing exactly what changed and any deviations from this plan.
- Not committing unless asked, per standing repo convention.

## Implementation Report

All five steps were applied to `agents/cckp-copilot/cloudformation.sql.yaml`'s `Instruction` block exactly as planned, with two deviations driven by information the user supplied only during plan review (not yet reflected in the "Approach" section above when it was first drafted):

1. **The plan's initial premise was corrected mid-review.** The first plan draft assumed no CCKP-native per-resource route existed and proposed a raw-Synapse-URL-only fix. The user rejected that and supplied the real, confirmed Detail Page URL patterns for Datasets and Publications directly, plus the general rule ("prioritize direct portal links when possible; don't link to Synapse unless the entity is a confirmed Dataset or Dataset Collection"). The plan was rewritten around this before implementation — this is reflected in the plan text above, not a silent deviation from it.
2. **Tools/Grants/Educational Resources patterns were clarified via AskUserQuestion**, since this repo had zero prior references to them and guessing exact URL param names/paths would have repeated the same "confident but wrong" failure mode this fix exists to correct. Confirmed answers: Tools use `/Explore/Tools/DetailsPage/Details?toolName={toolName}` (note the extra `/Details` path segment, unlike the other four types), Grants use `/Explore/Grants/DetailsPage?grantId={grantId}` (a distinct field from the `grantNumber` join key already referenced elsewhere in the prompt), Educational Resources use `/Explore/Educational%20Resources/DetailsPage?title={title}` (URL-encoded space, inconsistent with that type's own Collection-page path). All three are now documented in the new "Resource Detail Page Links" table with a caveat to confirm exact deployed column names via `getColumns` before relying on them, since none of the three key fields (`toolName`, `grantId`, `title`) had a verified backing column in this repo prior to this change.

**Changes applied** (all within the `Instruction` literal block, ~89 lines changed):

- Added a new "Resource Detail Page Links" section (replacing the old "Entity Details page redirects... not yet confirmed" note) with the full 5-resource-type URL table, the Educational-Resources encoding quirk callout, a `getColumns`-first caveat for the three unverified-column key fields, and the raw-Synapse-fallback rule scoped to Datasets/Dataset Collections only — including the user's additional requirement that the agent confirm an id actually came from a Dataset-typed source (`datasets` table's `id`/`downloadSynId`, or a `getDatasetFiles`/`checkRestriction` response) before ever constructing a `synapse.org` link from it.
- Defined `url` in the "Format most relevant hits as markdown links" guidance by pointing it at the new Detail Page table, and updated the worked example to use a real Detail Page URL inline instead of an undefined placeholder.
- Added a new worked sub-example showing single-resource navigation ("take me to Dataset Name 1's page") resolving to a `<actions><redirect>` targeting the Detail Page path — this is the literal scenario that previously had no defined behavior.
- In the "Dataset & File Discovery" section: clarified that a dataset row's `id` (Detail Page link) and `downloadSynId` (file discovery) are different values that must not be conflated, updated the worked example to show both values explicitly, and added the missing continuation showing what happens when the user takes the "Take me to the dataset's page" guideprompt — this was the exact scenario in the reported complaint, and previously ended with no example at all.
- Updated the restricted-access rule of engagement (was: "point the user to the resource's page") to reference the new Detail Page section by name instead of an undefined destination.

**Verification performed:**

- `agents/cckp-copilot/cloudformation.sql.yaml` parses successfully under a CloudFormation-aware YAML loader (plain `yaml.safe_load` fails on `!GetAtt`/`!Ref`/etc. intrinsic-function tags both before and after this change — confirmed via `git stash` that this is pre-existing baseline behavior, not something this change introduced).
- Grepped the full instruction block for `"not yet confirmed"`/`"unconfirmed"` afterward: the only remaining hit is the pre-existing, still-accurate note about People/Other resource types having no confirmed backing table or route at all (collection *or* detail) — unrelated to this fix, correctly left alone.
- Read through both edited worked examples end-to-end for internal consistency: SQL/query JSON shapes match the `SqlQueryRequest`/`GetDatasetFilesRequest` conventions used elsewhere in the same file, and both new `<actions><redirect>` blocks match the existing `<actions>` XML structure (a `<redirect><target>` with no `<query>` needed, since the target URL itself already carries the resource-identifying query param).
- No live CCKP agent is deployed yet (per `agents/README.md`), so this could not be tested against a real Bedrock invocation, as anticipated in the plan.

No other deviations from the approved plan. No Lambda/openapi files were touched (confirmed unnecessary, per the plan) — every key field used (`id`, `pubMedId`, `toolName`, `grantId`, `title`) is already returned as a plain column by `sqlQuery`'s existing `SELECT *` behavior.

### Addendum: `+`-encode spaces in `toolName`/`title` values

Follow-up correction after the initial implementation above: the user specified that spaces within the `toolName` and `title` *values* (not the fixed portal path segments) must be converted to `+`, not left as literal spaces or `%20`-encoded. Updated the "Resource Detail Page Links" table rows for Tools and Educational Resources plus added a short explanatory paragraph distinguishing this from the pre-existing `%20`-in-path quirk (which is fixed portal routing, unrelated to any row value) — with worked examples (`"Single Cell Explorer"` → `Single+Cell+Explorer`, `"Intro to Cancer Genomics"` → `Intro+to+Cancer+Genomics`) and an explicit note that `datasetId`/`pubMedId`/`grantId` don't need this since they aren't free-text names. Re-verified the file still parses under the CFN-aware YAML loader. No other files needed changes — the worked examples in the file only demonstrate the Datasets pattern, so none referenced `toolName`/`title` and none needed updating.
