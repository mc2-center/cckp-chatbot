import json
import os
import socket
import time
import urllib.error
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
        "/columns": "getColumns",
        "/count-by-type": "countByType",
        "/dataset-files": "getDatasetFiles",
        "/file-details": "getFileDetails",
        "/check-restriction": "checkRestriction",
    }
    return mapping.get(api_path)


def extract_params(event: Dict[str, Any]) -> Dict[str, Any]:
    request_body = event.get("requestBody", {})
    content = request_body.get("content", {})
    application_json = content.get("application/json", {})
    properties = application_json.get("properties", [])

    params: Dict[str, Any] = {}
    for prop in properties:
        params[prop["name"]] = prop["value"]

    for item in event.get("parameters", []):
        if "name" in item and "value" in item:
            params[item["name"]] = item["value"]

    return params


def _resolve_table(table: str) -> str:
    if table in TABLES:
        return TABLES[table]
    if table.startswith("syn"):
        return table
    raise ValueError(
        f"Unknown table {table!r}. Use a synId or one of: {', '.join(TABLES)}"
    )


def _request(method: str, url: str, body: Optional[dict] = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {SYNAPSE_AUTH_TOKEN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail)
        except ValueError:
            pass
        return e.code, detail
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


def get_dataset_files(params: Dict[str, Any]) -> Dict[str, Any]:
    """List the file contents of a Synapse entity backing a CCKP dataset row
    (e.g. a DatasetView row's `downloadSynId`).

    The entity may be a first-class Synapse Dataset (a curated flat list of
    file references, already embedded in its own JSON body) or a plain
    Folder/Project container (whose children must be listed separately).
    """
    entity_id = params.get("id")
    if not entity_id:
        return {"error": "id is required"}

    status, entity = _request("GET", f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}")
    if status not in (200, 201):
        return {"error": f"Failed to fetch entity {entity_id} (HTTP {status}): {entity}"}

    concrete_type = entity.get("concreteType", "")

    if concrete_type == DATASET_CONCRETE_TYPE:
        return {
            "type": "dataset",
            "items": entity.get("items", []),
            "count": entity.get("count"),
            "size": entity.get("size"),
            "checksum": entity.get("checksum"),
        }

    if concrete_type in CONTAINER_CONCRETE_TYPES:
        body = {
            "parentId": entity_id,
            "includeTypes": ["file", "folder"],
            "includeTotalChildCount": True,
            "includeSumFileSizes": True,
        }
        status, resp = _request(
            "POST", f"{SYNAPSE_BASE_URL}/repo/v1/entity/children", body
        )
        if status not in (200, 201):
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
    """
    entity_id = params.get("id")
    if not entity_id:
        return {"error": "id is required"}

    version = params.get("versionNumber")
    if version:
        url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}/version/{version}/filehandles"
    else:
        url = f"{SYNAPSE_BASE_URL}/repo/v1/entity/{entity_id}/filehandles"

    status, resp = _request("GET", url)
    if status not in (200, 201):
        return {"error": f"Failed to fetch file handles for {entity_id} (HTTP {status}): {resp}"}

    handles = resp.get("list", []) if isinstance(resp, dict) else []
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
    return {"id": entity_id, "files": files}


def check_restriction(params: Dict[str, Any]) -> Dict[str, Any]:
    """Check whether datasets/files are actually publicly accessible.

    Must be called before telling a user a resource is downloadable — CCKP is
    a public-data-only, read-only portal, and not every entity is OPEN.
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
    if status not in (200, 201):
        return {"error": f"Failed to check restriction info (HTTP {status}): {resp}"}

    restrictions = {}
    for item in resp.get("restrictionInformation", []):
        restrictions[str(item.get("objectId"))] = {
            "restrictionLevel": item.get("restrictionLevel"),
            "hasUnmetAccessRequirement": item.get("hasUnmetAccessRequirement"),
        }
    return {"restrictions": restrictions}
