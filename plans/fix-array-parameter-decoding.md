# Fix: array/object-typed action-group parameters aren't decoded from Bedrock's string-encoded values

## Context

User reported the dev agent repeatedly failing with `"searchExpressions must be a list of strings"` when asked to redirect to filtered breast-cancer RNA-seq datasets — retrying the identical call 4+ times with no success. The captured trace also shows a second, distinct failure: a `facets` call returning `"Failed to process request: 'str' object has no attribute 'get'"`.

Root cause (confirmed via AWS documentation, not assumption): every operation in this Lambda's OpenAPI schema uses a `requestBody` (POST-body) shape, and per AWS's own docs for the action-group Lambda input event —

> Source: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html ("Lambda input event from Amazon Bedrock") — every `requestBody.content.<type>.properties[]` entry has the shape `{"name": "string", "type": "string", "value": "string"}`. **`value` is always a string**, regardless of the property's declared `type` (string, array, or object). Corroborated independently by AWS's own `aws-lambda-powertools-python` library, which types `BedrockAgentProperty.value` as `str`, and by an AWS re:Post thread ("Bedrock Agent Action Group Not Passing Objects/Arrays as Valid JSON to Lambda") reporting that array values sometimes arrive as **malformed pseudo-JSON** — unquoted, comma-separated scalars like `"[customer, vip]"` rather than valid JSON `'["customer", "vip"]'`.

`extract_params` (`agents/cckp-copilot/lambda/cckpSqlRag/lambda_function.py`) takes `prop["value"]` verbatim with no decoding at all, for any property of any type. This means:

- `searchExpressions` (array of strings): the real trace shows it arrived as the literal string `"[breast cancer, RNA sequencing]"` — exactly the malformed pseudo-JSON shape AWS's own re:Post thread describes. `isinstance(value, list)` correctly rejects it, but every retry with the same (from the model's perspective, correct) input fails identically — the model has no way to work around a server-side bug by retrying.
- `facets` (array of objects): the model's emitted value was syntactically valid embedded JSON, but since it arrives as an outer *string* and is never `json.loads()`'d, `for facet in facets:` iterates over the string's individual **characters**, and `char.get("columnName")` throws `'str' object has no attribute 'get'`.
- `checkRestriction`'s `ids` (array of strings, `agents/cckp-copilot/lambda/cckpSqlRag/openapi.yaml`): has the same underlying exposure. Its current handling (`if isinstance(ids, str): ids = [i.strip() for i in ids.split(",")]`) only handles a plain comma-separated string, not a bracketed one — a real `"[syn1, syn2]"` value would silently corrupt to `["[syn1", "syn2]"]` (broken ids, not even an error) rather than working or failing loudly.

This bug **predates** this session's `searchExpressions` rename — `facets` has had this exposure since it was first added, it just had apparently never been exercised through a real live Bedrock invocation with an actual array-of-objects value until now. The test suite never caught it because `_function_event` (the test helper) always injects real Python lists/dicts directly as `value`, never the string-encoded form Bedrock actually sends — so unit tests structurally cannot see this class of bug.

## Approach

### 1. Decode array/object properties centrally in `extract_params`

Add a helper that JSON-decodes a property's string value when its declared `type` is `array` or `object`, with a fallback for the malformed-bracket case:

```python
def _coerce_property_value(declared_type, value):
    if declared_type not in ("array", "object") or not isinstance(value, str):
        return value
    text = value.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    if declared_type == "array" and text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [item.strip() for item in inner.split(",")] if inner else []
    return value
```

Apply it in `extract_params` for both the `requestBody.content.*.properties` loop and the `event["parameters"]` loop (same shape, same quirk applies to either), keyed off each entry's own `type` field.

This is a single, centralized fix — every current and future array/object parameter across all 7 functions benefits, rather than patching `searchExpressions`/`facets`/`ids` separately. Functions that already assume real lists/dicts (all of `build_explore_url`, `check_restriction`) need no further changes once `extract_params` hands them real Python values; their existing type/shape validation continues to serve as a safety net for genuinely malformed input.

### 2. Tests: simulate the real Bedrock wire format, not just Python-native values

The existing `_function_event` test helper only exercises the "parameters already a real list" path, which is why this shipped unnoticed. Add:

- A raw event fixture matching AWS's actual documented `requestBody.content["application/json"].properties` shape, with `value` as a **string** for `type: array` properties — both the clean-JSON case (`'["breast cancer", "RNA sequencing"]'`) and the malformed pseudo-JSON case (`'[breast cancer, RNA sequencing]'`, matching the real captured trace verbatim).
- Same for `facets` as a JSON-string-encoded array of objects.
- Same for `checkRestriction`'s `ids` as a bracketed string.
- Assert `extract_params` (or the full `lambda_handler` round trip) produces correctly-typed Python lists/dicts in every case, and that `build_explore_url`/`check_restriction` then behave exactly as their existing tests already expect.

## Verification

- Run the full test suite (`python3 -m pytest tests/ -v`), all passing including new cases.
- Re-parse `cloudformation.sql.yaml` with the project's CFN-aware loader (no schema changes expected here — this is a Lambda-internal fix, not an OpenAPI shape change — but re-verify nothing else regressed).
- Manually construct a real `requestBody`-shaped event matching the captured trace's exact malformed `searchExpressions` value and confirm `lambda_handler` now returns a working `url` instead of the validation error.

## Process

- Store this plan at `plans/fix-array-parameter-decoding.md` for review before implementing.
- After implementing, append an Implementation Report noting changes applied, deviations, and verification results.

## Implementation Report

Both approach steps applied as planned, no deviations:

1. **`lambda_function.py`**: added `_coerce_property_value(declared_type, value)` exactly as designed — passes non-array/object types and non-string values through untouched, otherwise tries `json.loads()` first, falling back to a manual bracket/comma parse for the malformed pseudo-JSON case. `extract_params` now calls it for every property in both the `requestBody.content.*.properties` loop and the `event["parameters"]` loop, keyed off each entry's own `type` field. No changes were needed in `build_explore_url` or `check_restriction` themselves — once `extract_params` hands them real Python lists/dicts, their existing validation and logic already do the right thing.
2. **Tests**: added to the existing `_api_event`-based `TestExtractParams` class (clean-JSON array, malformed-pseudo-JSON array, object-array/facets, scalar-passthrough, already-a-list-passthrough), a new `TestCoercePropertyValue` class for the helper's edge cases directly (non-array/object type, empty bracket, unparseable-and-not-bracketed), and three new `TestBuildExploreUrl` cases plus one new `TestCheckRestriction` case that reproduce the exact real Bedrock wire format (via `_api_event` with `type`/string `value`) for `searchExpressions`, `facets`, and `ids` respectively — including the malformed-pseudo-JSON shape copied verbatim from the user's captured trace. Full suite: **94/94 passing** (82 prior + 12 new).

### Verification

- `python3 -m pytest tests/ -v` — 94/94 passing.
- Reconstructed the exact failing event from the user's captured trace (`searchExpressions` property with `type: array`, `value: "[breast cancer, RNA sequencing]"`) and called `lambda_handler` directly: previously this returned `{"error": "searchExpressions must be a list of strings"}` (matching the live failure); after the fix it returns a working `url`.
- Took that real `url` output and hard-navigated to it on staging: **"1,061 results filtered by breast cancer, RNA sequencing"** — two separate filter chips, confirming the full path (Bedrock's real malformed wire format → `extract_params` decoding → `build_explore_url` → live portal rendering) now works end-to-end, not just in isolated unit tests.
- No `cloudformation.sql.yaml`/OpenAPI schema changes were needed — this was a Lambda-internal parsing fix, not a schema shape change.
