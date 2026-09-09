import base64
import gzip
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


SYNAPSE_BASE_URL = os.environ.get(
    "SYNAPSE_BASE_URL", "https://repo-prod.prod.sagebase.org"
)
SYNAPSE_AUTH_TOKEN = os.environ.get("SYNAPSE_AUTH_TOKEN", "")

# LAMBDA_TIMEOUT should match the Lambda function's configured timeout (seconds).
# QUERY_TIMEOUT is kept shorter so the Lambda always has time to return a clean
# error response before AWS forcibly kills the invocation (which causes a 424).
_LAMBDA_TIMEOUT = int(os.environ.get("LAMBDA_TIMEOUT", "30"))
QUERY_TIMEOUT = int(os.environ.get("QUERY_TIMEOUT", str(max(_LAMBDA_TIMEOUT - 5, 5))))

# Confirmed CCKP View tables in Synapse project syn21498902. Re-verify with
# `getColumns` against a raw synId if a query ever errors on an unknown table.
TABLES = {
    "datasets": "syn21897968",
    "publications": "syn21868591",
    "tools": "syn26127427",
    "grants": "syn21918972",
    "education": "syn51497305",
}

# Portal Explore path segment per table alias. "education" has a literal
# space — confirmed against the live site's own nav tab href
# ("/Explore/Educational Resources"), not "EducationalResources".
RESOURCE_PATHS = {
    "datasets": "Datasets",
    "publications": "Publications",
    "tools": "Tools",
    "grants": "Grants",
    "education": "Educational Resources",
}

# partMask bits (Synapse): query results = 0x1, count = 0x2, select columns = 0x4
PART_RESULTS = 0x1
PART_COUNT = 0x2
PART_SELECT_COLUMNS = 0x4

DEFAULT_LIMIT = 25
MAX_LIMIT = 200

# concreteType values relevant to dataset/file discovery
DATASET_CONCRETE_TYPE = "org.sagebionetworks.repo.model.table.Dataset"
CONTAINER_CONCRETE_TYPES = {
    "org.sagebionetworks.repo.model.Folder",
    "org.sagebionetworks.repo.model.Project",
}

MAX_RESTRICTION_IDS = 50


def _make_response(action_group, api_path, http_method, http_status, body):
    """Build a Bedrock action-group response that is always JSON-serializable."""
    try:
        body_str = json.dumps(body)
    except (TypeError, ValueError):
        body_str = json.dumps({"error": "Response could not be serialized"})
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": http_method,
            "httpStatusCode": http_status,
            "responseBody": {
                "application/json": {"body": body_str}
            },
        },
    }


def lambda_handler(event, context):
    """
    Lambda handler for exposing the cckp_rag SQL helper functions to an agent.

    Queries the CCKP's curated Synapse View tables (Dataset, Publication,
    Tool, Grant, EducationalResource) directly via the Synapse table-query
    REST API, rather than a knowledge graph. The entire body is wrapped in a
    top-level try/except so that a well-formed response is *always* returned;
    an unhandled exception would cause an AWS invocation failure, which
    Bedrock surfaces as a 424 error.
    """
    action_group = api_path = http_method = None
    try:
        print(f"Received event: {json.dumps(event)}")

        action_group = event.get("actionGroup")
        api_path = event.get("apiPath", "")
        http_method = event.get("httpMethod", "POST")
        function = map_api_path_to_function(api_path) or event.get("function")
        params = extract_params(event)

        print(f"API path: {api_path}, function: {function}, params: {params}")

        if function == "sqlQuery":
            response_body = sql_query(params)
        elif function == "buildExploreUrl":
            response_body = build_explore_url(params)
        elif function == "getColumns":
            response_body = get_columns_fn(params)
        elif function == "countByType":
            response_body = count_by_type(params)
        elif function == "getDatasetFiles":
            response_body = get_dataset_files(params)
        elif function == "getFileDetails":
            response_body = get_file_details(params)
        elif function == "checkRestriction":
            response_body = check_restriction(params)
        else:
            response_body = {
                "error": f"Unknown function: {function}",
                "apiPath": api_path,
                "eventKeys": list(event.keys()),
            }

        response = _make_response(
            action_group, api_path, http_method, 200, response_body
        )
    except TimeoutError as e:
        print(f"Timeout: {e}")
        response = _make_response(
            action_group, api_path, http_method, 200, {"error": str(e)}
        )
    except Exception as e:
        print(f"Error processing request: {e}")
        response = _make_response(
            action_group, api_path, http_method, 200,
            {"error": f"Failed to process request: {e}"},
        )

    print(f"Returning response: {json.dumps(response)}")
    return response


def map_api_path_to_function(api_path: str) -> Optional[str]:
    mapping = {
        "/sql-query": "sqlQuery",
        "/explore-url": "buildExploreUrl",
        "/columns": "getColumns",
        "/count-by-type": "countByType",
        "/dataset-files": "getDatasetFiles",
        "/file-details": "getFileDetails",
        "/check-restriction": "checkRestriction",
    }
    return mapping.get(api_path)


def _coerce_property_value(declared_type: Optional[str], value: Any) -> Any:
    """Decode a Bedrock action-group property value into a real Python type.

    Bedrock always sends `value` as a string in the Lambda invocation event,
    even for properties whose OpenAPI schema declares `type: array` or
    `type: object` — confirmed via AWS's own docs for the action-group
    Lambda input event (agents-lambda.html), which show every `properties`
    entry as `{"name": "string", "type": "string", "value": "string"}` with
    no exception for non-scalar types.

    Worse, array values sometimes arrive as malformed pseudo-JSON — bracketed
    but with bare, unquoted, comma-separated scalars, e.g.
    "[breast cancer, RNA sequencing]" instead of valid JSON
    '["breast cancer", "RNA sequencing"]' — a quirk also independently
    reported on AWS re:Post, not something specific to this Lambda. A plain
    `json.loads` alone doesn't cover that case, so this falls back to a
    manual bracket/comma parse when JSON decoding fails.
    """
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


def extract_params(event: Dict[str, Any]) -> Dict[str, Any]:
    request_body = event.get("requestBody", {})
    content = request_body.get("content", {})
    application_json = content.get("application/json", {})
    properties = application_json.get("properties", [])

    params: Dict[str, Any] = {}
    for prop in properties:
        if "name" in prop and "value" in prop:
            params[prop["name"]] = _coerce_property_value(prop.get("type"), prop["value"])

    for item in event.get("parameters", []):
        if "name" in item and "value" in item:
            params[item["name"]] = _coerce_property_value(item.get("type"), item["value"])

    return params


def _resolve_table(table: str) -> str:
    if table in TABLES:
        return TABLES[table]
    if table.startswith("syn"):
        return table
    raise ValueError(
        f"Unknown table {table!r}. Use a synId or one of: {', '.join(TABLES)}"
    )


def _parse_response_body(text: str) -> Any:
    """Best-effort JSON decode.

    Falls back to a raw-text wrapper on non-JSON/malformed bodies so callers
    can always call .get() on the result without raising.
    """
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


def _request(method: str, url: str, body: Optional[dict] = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {SYNAPSE_AUTH_TOKEN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as resp:
            return resp.status, _parse_response_body(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return e.code, _parse_response_body(detail)
    except (urllib.error.URLError, socket.timeout) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(f"Synapse query timed out after {QUERY_TIMEOUT}s")
        raise Exception(f"Request failed: {e}")


def _run_query(syn_id: str, sql: str, limit: int, part_mask: int) -> dict:
    """Start an async table query against one synId, poll, return the raw bundle."""
    start_url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{syn_id}/table/query/async/start"
    body = {
        "concreteType": "org.sagebionetworks.repo.model.table.QueryBundleRequest",
        "entityId": syn_id,
        "query": {"sql": sql, "limit": limit},
        "partMask": part_mask,
    }
    status, resp = _request("POST", start_url, body)
    if status not in (200, 201) or not isinstance(resp, dict) or "token" not in resp:
        raise Exception(f"query start failed (HTTP {status}): {resp}")
    token = resp["token"]

    get_url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{syn_id}/table/query/async/get/{token}"
    deadline = time.monotonic() + QUERY_TIMEOUT
    while True:
        status, resp = _request("GET", get_url)
        if status in (200, 201):
            return resp
        if status == 202:  # still processing
            if time.monotonic() >= deadline:
                raise TimeoutError("Synapse query is still running")
            time.sleep(1.0)
            continue
        raise Exception(f"query failed (HTTP {status}): {resp}")


def _parse_bundle(bundle: dict) -> dict:
    """Flatten a QueryResultBundle into {count, headers, rows[dict]}."""
    qr = ((bundle or {}).get("queryResult") or {}).get("queryResults") or {}
    headers = [h.get("name") for h in qr.get("headers", [])]
    rows = []
    for row in qr.get("rows", []):
        record = dict(zip(headers, row.get("values", [])))
        rows.append(record)
    return {"count": bundle.get("queryCount"), "headers": headers, "rows": rows}


def _clamp_limit(limit: Any) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def sql_query(params: Dict[str, Any]) -> Dict[str, Any]:
    table = params.get("table")
    sql = params.get("sql")
    if not table:
        return {"error": "table is required"}
    if not sql:
        return {"error": "sql is required"}

    try:
        syn_id = _resolve_table(table)
    except ValueError as e:
        return {"error": str(e)}

    sql = sql.replace("{table}", syn_id)
    limit = _clamp_limit(params.get("limit", DEFAULT_LIMIT))
    bundle = _run_query(syn_id, sql, limit, PART_RESULTS | PART_COUNT)
    return _parse_bundle(bundle)


def build_explore_url(params: Dict[str, Any]) -> Dict[str, Any]:
    """Build a working CCKP portal Explore path, optionally pre-filtered.

    The portal's Explore pages read search/filter state from a `qw0` query
    param: gzip-compressed, base64-encoded, URL-encoded JSON. This can't be
    produced by an LLM as generated text (gzip is a binary format with
    checksums), so it's computed here instead — the caller gets back a
    ready-to-use path and should use it verbatim as a redirect target, no
    further assembly needed.

    Returns a *relative* path (e.g. "/Explore/Datasets/?qw0=..."), matching
    every other redirect target this agent has ever used — a plain literal
    Collection/Detail Page path is always relative, never a full URL with a
    domain. An earlier version of this function returned an absolute URL
    (with the https://... domain prefix), which broke redirects in
    practice: the chat frontend's redirect handler expects `<target>` to be
    a path it resolves against its own base URL, not an already-absolute
    URL to use as-is.

    IMPORTANT: the portal ignores any custom WHERE clause placed in the
    `sql` field of this query object — confirmed by live-testing against
    staging.cancercomplexity.synapse.org. It always reconstructs its own
    query from `additionalFilters` (free-text search) and `selectedFacets`
    (exact-value column filters), so filtering here must go through
    `searchExpressions`/`facets`, not a hand-written SQL WHERE clause. The
    `sql` this function sends is always the bare `SELECT * FROM {tableId}`.

    `searchExpressions` is a list, not a single string: each entry becomes
    its own independent `TextMatchesQueryFilter`, rendered by the portal as
    a separate, independently-removable filter chip. Passing ["glioma",
    "single cell RNA sequencing"] produces two chips ("glioma" and "single
    cell RNA sequencing"); this is different from passing one merged
    string ("glioma single cell RNA sequencing"), which produces a single
    chip searched as one phrase.

    IMPORTANT — multiple `searchExpressions` entries do NOT reliably AND.
    An earlier version of this docstring claimed they did ("confirmed live
    against staging"); that claim was never actually discriminating (see
    plans/combined-filter-search-expressions.md's correction addendum) and
    is wrong. Live-tested against *production*, two independent concepts
    ("glioma": 230 results alone; "fluorescence microscopy": 1,273 alone)
    combined to **1,449** results — more than either term alone, which is
    only possible if the filters are OR'd/unioned, not intersected (230 +
    1,273 − 1,449 ≈ 54 overlap, consistent with a union). This held
    regardless of `searchMode` (`NATURAL_LANGUAGE` vs `BOOLEAN`) and
    regardless of whether `+`-prefixed required-term operators were used,
    as a single combined entry or as separate entries — all four shapes
    produced the identical 1,449, meaning `searchMode`/operators aren't
    honored for AND purposes on this deployment. For a filter that must
    narrow to a true intersection of ≥2 concepts, use `facets` instead —
    `selectedFacets` entries are confirmed to AND correctly (live-tested:
    tumorType=["Glioma"] (108 alone) + assay=["Cyclic Immunofluorescence"]
    (45 alone) → 1 result, a real intersection). Treat a multi-entry
    `searchExpressions` result count as an upper bound, not a precise
    filter, and default to `facets` whenever the concepts map to a
    facetable column (see plans/fix-searchexpressions-and-semantics.md).

    `table` must be one of the 5 known aliases (not a raw synId) since the
    Explore path segment is derived from it, not just the table's synId.
    """
    table = params.get("table")
    if not table:
        return {"error": "table is required"}
    if table not in RESOURCE_PATHS:
        return {
            "error": (
                f"Unknown table alias {table!r} for URL building. Use one "
                f"of: {', '.join(RESOURCE_PATHS)} (a raw synId can't be "
                "mapped to an Explore path)."
            )
        }

    syn_id = TABLES[table]

    query: Dict[str, Any] = {
        "sql": f"SELECT * FROM {syn_id}",
        "includeEntityEtag": False,
        "isConsistent": True,
    }

    search_expressions = params.get("searchExpressions")
    if search_expressions:
        if not isinstance(search_expressions, list):
            return {"error": "searchExpressions must be a list of strings"}
        additional_filters = []
        for expression in search_expressions:
            if not expression or not isinstance(expression, str):
                return {"error": "each searchExpressions entry must be a non-empty string"}
            additional_filters.append({
                "concreteType": "org.sagebionetworks.repo.model.table.TextMatchesQueryFilter",
                "searchExpression": expression,
                "searchMode": "NATURAL_LANGUAGE",
            })
        query["additionalFilters"] = additional_filters

    facets = params.get("facets")
    if facets:
        selected_facets = []
        for facet in facets:
            column_name = facet.get("columnName")
            values = facet.get("values")
            if not column_name or not values:
                return {"error": "each facet requires columnName and values"}
            selected_facets.append({
                "concreteType": "org.sagebionetworks.repo.model.table.FacetColumnValuesRequest",
                "columnName": column_name,
                "facetValues": values,
            })
        query["selectedFacets"] = selected_facets

    payload = json.dumps(query, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(payload)
    qw0 = urllib.parse.quote(base64.b64encode(compressed).decode("ascii"))

    # Self-verify before returning: decode our own qw0 back to the query we
    # just built. This only catches a bug in this function's own encoding
    # (e.g. a future change to the gzip/base64/urlencode steps) — it can't
    # catch the model mangling the string afterward, since that happens
    # downstream of this return value. Still worth doing: better to return
    # an explicit error here than a URL that silently doesn't work.
    try:
        roundtrip = json.loads(gzip.decompress(base64.b64decode(urllib.parse.unquote(qw0))))
    except Exception as e:
        return {"error": f"internal error: qw0 failed to self-verify ({e})"}
    if roundtrip != query:
        return {"error": "internal error: qw0 self-verification mismatch"}

    path_segment = urllib.parse.quote(RESOURCE_PATHS[table])
    url = f"/Explore/{path_segment}/?qw0={qw0}"
    return {"url": url}


def get_columns_fn(params: Dict[str, Any]) -> Dict[str, Any]:
    table = params.get("table")
    if not table:
        return {"error": "table is required"}

    try:
        syn_id = _resolve_table(table)
    except ValueError as e:
        return {"error": str(e)}

    bundle = _run_query(
        syn_id, f"SELECT * FROM {syn_id} LIMIT 1", 1, PART_RESULTS | PART_SELECT_COLUMNS
    )
    columns = [c.get("name") for c in (bundle.get("selectColumns") or [])]
    if not columns:  # fall back to the result-set headers
        columns = _parse_bundle(bundle)["headers"]
    return {"table": table, "columns": columns}


def count_by_type(params: Dict[str, Any]) -> Dict[str, Any]:
    counts = {}
    errors = {}
    for alias, syn_id in TABLES.items():
        try:
            bundle = _run_query(syn_id, f"SELECT * FROM {syn_id}", 1, PART_COUNT)
            counts[alias] = bundle.get("queryCount")
        except Exception as e:
            errors[alias] = str(e)
    result = {"counts": counts}
    if errors:
        result["errors"] = errors
    return result


def _check_single_restriction(entity_id: str) -> Dict[str, Any]:
    """Best-effort restriction check for one id — never raises, so a hiccup
    here doesn't block the primary discovery call it's embedded in."""
    try:
        result = check_restriction({"ids": [entity_id]})
    except Exception as e:
        return {"error": str(e)}
    if "error" in result:
        return {"error": result["error"]}
    info = result.get("restrictions", {}).get(entity_id)
    return info if info is not None else {"error": "No restriction information returned"}


def get_dataset_files(params: Dict[str, Any]) -> Dict[str, Any]:
    """List the file contents of a Synapse entity backing a CCKP dataset row
    (e.g. a DatasetView row's `downloadSynId`).

    The entity may be a first-class Synapse Dataset (a curated flat list of
    file references, already embedded in its own JSON body) or a plain
    Folder/Project container (whose children must be listed separately).

    Always includes a `restriction` field so callers don't have to remember a
    separate checkRestriction call before treating the result as accessible.
    """
    entity_id = params.get("id")
    if not entity_id:
        return {"error": "id is required"}

    status, entity = _request("GET", f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}")
    if status not in (200, 201) or not isinstance(entity, dict):
        return {"error": f"Failed to fetch entity {entity_id} (HTTP {status}): {entity}"}

    concrete_type = entity.get("concreteType", "")

    if concrete_type == DATASET_CONCRETE_TYPE:
        items = entity.get("items") or []
        limit = _clamp_limit(params.get("limit", DEFAULT_LIMIT))
        result = {
            "type": "dataset",
            "items": items[:limit],
            "returnedCount": min(len(items), limit),
            "count": entity.get("count"),
            "size": entity.get("size"),
            "checksum": entity.get("checksum"),
        }
        result["restriction"] = _check_single_restriction(entity_id)
        return result

    if concrete_type in CONTAINER_CONCRETE_TYPES:
        body = {
            "parentId": entity_id,
            "includeTypes": ["file", "folder"],
            "includeTotalChildCount": True,
            "includeSumFileSizes": True,
        }
        next_page_token = params.get("nextPageToken")
        if next_page_token:
            body["nextPageToken"] = next_page_token

        status, resp = _request(
            "POST", f"{SYNAPSE_BASE_URL}/repo/v1/entity/children", body
        )
        if status not in (200, 201) or not isinstance(resp, dict):
            return {"error": f"Failed to list children of {entity_id} (HTTP {status}): {resp}"}

        children = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "type": c.get("type"),
                "versionNumber": c.get("versionNumber"),
            }
            for c in resp.get("page", [])
        ]
        result = {
            "type": "container",
            "children": children,
            "totalChildCount": resp.get("totalChildCount"),
            "sumFileSizesBytes": resp.get("sumFileSizesBytes"),
        }
        if resp.get("nextPageToken"):
            result["nextPageToken"] = resp["nextPageToken"]
        result["restriction"] = _check_single_restriction(entity_id)
        return result

    return {
        "error": (
            f"Entity {entity_id} is a {concrete_type or 'unknown type'}, "
            "not a Dataset or container — no file contents to list."
        )
    }


def get_file_details(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return real file metadata (name/size/type/checksum) for a file entity.

    A bare entity fetch only returns a dataFileHandleId reference; the actual
    file metadata lives on the FileHandle(s) associated with the entity.

    Always includes a `restriction` field so callers don't have to remember a
    separate checkRestriction call before treating the result as accessible.
    """
    entity_id = params.get("id")
    if not entity_id:
        return {"error": "id is required"}

    version = params.get("versionNumber")
    if version not in (None, ""):
        try:
            version = int(version)
        except (TypeError, ValueError):
            return {"error": f"versionNumber must be an integer, got {version!r}"}
        url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}/version/{version}/filehandles"
    else:
        url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}/filehandles"

    status, resp = _request("GET", url)
    if status not in (200, 201) or not isinstance(resp, dict):
        return {"error": f"Failed to fetch file handles for {entity_id} (HTTP {status}): {resp}"}

    handles = resp.get("list") or []
    files = [
        {
            "fileHandleId": h.get("id"),
            "fileName": h.get("fileName"),
            "contentSize": h.get("contentSize"),
            "contentType": h.get("contentType"),
            "contentMd5": h.get("contentMd5"),
        }
        for h in handles
    ]
    return {
        "id": entity_id,
        "files": files,
        "restriction": _check_single_restriction(entity_id),
    }


def check_restriction(params: Dict[str, Any]) -> Dict[str, Any]:
    """Check whether datasets/files are actually publicly accessible.

    Must be called before telling a user a resource is downloadable — CCKP is
    a public-data-only, read-only portal, and not every entity is OPEN.
    (getDatasetFiles/getFileDetails already embed this per-id, so this
    function is mainly for checking ids directly, or checking several at once.)
    """
    ids = params.get("ids")
    if not ids:
        return {"error": "ids is required"}

    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",") if i.strip()]

    if len(ids) > MAX_RESTRICTION_IDS:
        return {"error": f"at most {MAX_RESTRICTION_IDS} ids allowed per call"}

    body = {"restrictableObjectType": "ENTITY", "objectIds": ids}
    status, resp = _request(
        "POST", f"{SYNAPSE_BASE_URL}/repo/v1/restrictionInformation/batch", body
    )
    if status not in (200, 201) or not isinstance(resp, dict):
        return {"error": f"Failed to check restriction info (HTTP {status}): {resp}"}

    restrictions = {}
    for item in resp.get("restrictionInformation", []):
        object_id = item.get("objectId")
        # objectId comes back as a bare integer (e.g. 12345678); the request
        # was made in terms of "synXXXXXXX" ids, so restore that form to make
        # the result keyed the same way callers passed ids in.
        key = f"syn{object_id}" if object_id is not None else str(object_id)
        restrictions[key] = {
            "restrictionLevel": item.get("restrictionLevel"),
            "hasUnmetAccessRequirement": item.get("hasUnmetAccessRequirement"),
        }
    return {"restrictions": restrictions}
