"""Unit tests for the cckpSqlRag Lambda function.

All Synapse network calls are mocked so no live token/endpoint is needed.
"""

import base64
import gzip
import json
import socket
import urllib.error
import urllib.parse
from unittest.mock import patch

import pytest

from lambda_function import (
    _coerce_property_value,
    _make_response,
    _parse_bundle,
    _resolve_table,
    build_explore_url,
    extract_params,
    lambda_handler,
    RESOURCE_PATHS,
    TABLES,
)


def _decode_qw0(url):
    """Reverse the gzip+base64+urlencode round-trip to inspect the query object."""
    qw0 = url.split("qw0=", 1)[1]
    raw = base64.b64decode(urllib.parse.unquote(qw0))
    return json.loads(gzip.decompress(raw))


# Precomputed at import time — deliberately the "wrong" payload for
# TestBuildExploreUrl.test_self_verification_catches_encoding_bug. Must be
# computed before that test's @patch("lambda_function.gzip.compress") is
# active, since that patch replaces the real gzip.compress process-wide.
_WRONG_COMPRESSED_PAYLOAD = gzip.compress(b'{"not": "the real query"}')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_event(api_path, properties=None, http_method="POST"):
    """Build a Bedrock action-group event using the apiPath style."""
    return {
        "messageVersion": "1.0",
        "actionGroup": "cckpSqlRag",
        "apiPath": api_path,
        "httpMethod": http_method,
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": properties or [],
                }
            }
        },
    }


def _function_event(function, parameters=None):
    """Build a Bedrock action-group event using the function style."""
    return {
        "messageVersion": "1.0",
        "actionGroup": "cckpSqlRag",
        "function": function,
        "parameters": parameters or [],
    }


def _body(response):
    """Extract the parsed JSON body from a Lambda response dict."""
    return json.loads(
        response["response"]["responseBody"]["application/json"]["body"]
    )


def _bundle(headers, rows, count=None):
    """Build a fake QueryResultBundle as returned by the Synapse REST API."""
    return {
        "queryCount": count if count is not None else len(rows),
        "queryResult": {
            "queryResults": {
                "headers": [{"name": h} for h in headers],
                "rows": [{"values": r} for r in rows],
            }
        },
    }


# ---------------------------------------------------------------------------
# _make_response
# ---------------------------------------------------------------------------

class TestMakeResponse:
    def test_normal_body(self):
        resp = _make_response("ag", "/path", "POST", 200, {"ok": True})
        assert resp["response"]["httpStatusCode"] == 200
        assert json.loads(resp["response"]["responseBody"]["application/json"]["body"]) == {"ok": True}

    def test_non_serializable_body(self):
        resp = _make_response("ag", "/path", "POST", 200, {"bad": object()})
        body = json.loads(resp["response"]["responseBody"]["application/json"]["body"])
        assert "error" in body
        assert "serialized" in body["error"]


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------

class TestExtractParams:
    def test_from_request_body(self):
        event = _api_event("/sql-query", [
            {"name": "table", "type": "string", "value": "datasets"},
            {"name": "sql", "type": "string", "value": "SELECT * FROM {table}"},
        ])
        assert extract_params(event) == {
            "table": "datasets",
            "sql": "SELECT * FROM {table}",
        }

    def test_from_parameters_list(self):
        event = _function_event("getColumns", [
            {"name": "table", "type": "string", "value": "datasets"},
        ])
        assert extract_params(event) == {"table": "datasets"}

    def test_empty_event(self):
        assert extract_params({}) == {}

    def test_malformed_property_skipped(self):
        event = _api_event("/sql-query", [{"wrong_key": "oops"}])
        assert extract_params(event) == {}

    def test_malformed_property_mixed_with_valid(self):
        event = _api_event("/sql-query", [
            {"wrong_key": "oops"},
            {"name": "table", "value": "datasets"},
        ])
        assert extract_params(event) == {"table": "datasets"}

    def test_array_typed_value_decoded_from_json_string(self):
        # Real Bedrock requestBody events deliver array-typed values as a
        # JSON-encoded string, not a native list (confirmed via AWS docs —
        # see _coerce_property_value's docstring).
        event = _api_event("/check-restriction", [
            {"name": "ids", "type": "array", "value": '["syn1", "syn2"]'},
        ])
        assert extract_params(event) == {"ids": ["syn1", "syn2"]}

    def test_array_typed_value_decoded_from_malformed_pseudo_json(self):
        event = _api_event("/explore-url", [
            {"name": "searchExpressions", "type": "array", "value": "[breast cancer, RNA sequencing]"},
        ])
        assert extract_params(event) == {
            "searchExpressions": ["breast cancer", "RNA sequencing"],
        }

    def test_object_array_typed_value_decoded(self):
        facets_json = json.dumps([{"columnName": "species", "values": ["Zebrafish"]}])
        event = _api_event("/explore-url", [
            {"name": "facets", "type": "array", "value": facets_json},
        ])
        assert extract_params(event) == {
            "facets": [{"columnName": "species", "values": ["Zebrafish"]}],
        }

    def test_scalar_typed_value_left_alone(self):
        event = _api_event("/sql-query", [
            {"name": "table", "type": "string", "value": "datasets"},
        ])
        assert extract_params(event) == {"table": "datasets"}

    def test_array_value_already_a_list_passes_through(self):
        # Direct/synthetic invocations (and possibly future correctly-typed
        # Bedrock behavior) may hand over a real list already — must not be
        # altered.
        event = _api_event("/explore-url", [
            {"name": "searchExpressions", "type": "array", "value": ["glioma"]},
        ])
        assert extract_params(event) == {"searchExpressions": ["glioma"]}


class TestCoercePropertyValue:
    def test_non_array_object_type_passthrough(self):
        assert _coerce_property_value("string", "[not, touched]") == "[not, touched]"

    def test_empty_bracket_array(self):
        assert _coerce_property_value("array", "[]") == []

    def test_unparseable_non_bracketed_string_returned_as_is(self):
        # Not valid JSON and not bracketed — nothing safe to do; let the
        # caller's own validation produce a clear error instead of guessing.
        assert _coerce_property_value("array", "not a list at all") == "not a list at all"


# ---------------------------------------------------------------------------
# _resolve_table
# ---------------------------------------------------------------------------

class TestResolveTable:
    @pytest.mark.parametrize("alias", list(TABLES))
    def test_known_alias(self, alias):
        assert _resolve_table(alias) == TABLES[alias]

    def test_raw_syn_id_passthrough(self):
        assert _resolve_table("syn12345678") == "syn12345678"

    def test_unknown_table_raises(self):
        with pytest.raises(ValueError, match="Unknown table"):
            _resolve_table("bogus")


# ---------------------------------------------------------------------------
# _parse_bundle
# ---------------------------------------------------------------------------

class TestParseBundle:
    def test_flattens_rows(self):
        bundle = _bundle(["datasetName", "tumorType"], [["Foo", "Breast"]])
        result = _parse_bundle(bundle)
        assert result == {
            "count": 1,
            "headers": ["datasetName", "tumorType"],
            "rows": [{"datasetName": "Foo", "tumorType": "Breast"}],
        }

    def test_empty_bundle(self):
        assert _parse_bundle({}) == {"count": None, "headers": [], "rows": []}


# ---------------------------------------------------------------------------
# lambda_handler – routing
# ---------------------------------------------------------------------------

class TestHandlerRouting:
    """Each function is reached via both apiPath and function-name dispatch."""

    @patch("lambda_function._run_query")
    def test_sql_query_api_path(self, mock_run_query):
        mock_run_query.return_value = _bundle(["datasetName"], [["Foo"]])
        event = _api_event("/sql-query", [
            {"name": "table", "value": "datasets"},
            {"name": "sql", "value": "SELECT * FROM {table}"},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["rows"] == [{"datasetName": "Foo"}]
        # {table} placeholder was replaced with the resolved synId
        called_sql = mock_run_query.call_args[0][1]
        assert "{table}" not in called_sql
        assert TABLES["datasets"] in called_sql

    @patch("lambda_function._run_query")
    def test_sql_query_function(self, mock_run_query):
        mock_run_query.return_value = _bundle(["datasetName"], [["Foo"]])
        event = _function_event("sqlQuery", [
            {"name": "table", "value": "datasets"},
            {"name": "sql", "value": "SELECT * FROM {table}"},
        ])
        resp = lambda_handler(event, None)
        assert _body(resp)["rows"] == [{"datasetName": "Foo"}]

    @patch("lambda_function._run_query")
    def test_get_columns_api_path(self, mock_run_query):
        mock_run_query.return_value = {
            "selectColumns": [{"name": "datasetName"}, {"name": "tumorType"}],
            "queryResult": {"queryResults": {"headers": [], "rows": []}},
        }
        event = _api_event("/columns", [{"name": "table", "value": "datasets"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["table"] == "datasets"
        assert body["columns"] == ["datasetName", "tumorType"]

    @patch("lambda_function._run_query")
    def test_get_columns_function(self, mock_run_query):
        mock_run_query.return_value = {
            "selectColumns": [{"name": "toolName"}],
            "queryResult": {"queryResults": {"headers": [], "rows": []}},
        }
        resp = lambda_handler(
            _function_event("getColumns", [{"name": "table", "value": "tools"}]), None
        )
        assert _body(resp)["columns"] == ["toolName"]

    @patch("lambda_function._run_query")
    def test_count_by_type_api_path(self, mock_run_query):
        mock_run_query.return_value = _bundle([], [], count=42)
        resp = lambda_handler(_api_event("/count-by-type"), None)
        body = _body(resp)
        assert set(body["counts"]) == set(TABLES)
        assert all(v == 42 for v in body["counts"].values())

    @patch("lambda_function._run_query")
    def test_count_by_type_function(self, mock_run_query):
        mock_run_query.return_value = _bundle([], [], count=7)
        resp = lambda_handler(_function_event("countByType"), None)
        assert _body(resp)["counts"]["datasets"] == 7

    @patch("lambda_function._run_query", side_effect=Exception("boom"))
    def test_count_by_type_partial_failure(self, _mock):
        resp = lambda_handler(_function_event("countByType"), None)
        body = _body(resp)
        assert "errors" in body
        assert all("boom" in msg for msg in body["errors"].values())

    def test_unknown_function(self):
        event = _function_event("noSuchFunction")
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert "error" in body
        assert "Unknown function" in body["error"]
        assert resp["response"]["httpStatusCode"] == 200


# ---------------------------------------------------------------------------
# lambda_handler – validation & error handling (424-prevention)
# ---------------------------------------------------------------------------

class TestHandlerErrorPaths:
    """Verify the handler always returns a well-formed response."""

    def test_missing_table_returns_validation_error(self):
        event = _api_event("/sql-query", [{"name": "sql", "value": "SELECT 1"}])
        resp = lambda_handler(event, None)
        assert _body(resp) == {"error": "table is required"}

    def test_missing_sql_returns_validation_error(self):
        event = _api_event("/sql-query", [{"name": "table", "value": "datasets"}])
        resp = lambda_handler(event, None)
        assert _body(resp) == {"error": "sql is required"}

    def test_unknown_table_returns_error(self):
        event = _api_event("/sql-query", [
            {"name": "table", "value": "bogus"},
            {"name": "sql", "value": "SELECT * FROM {table}"},
        ])
        resp = lambda_handler(event, None)
        assert "Unknown table" in _body(resp)["error"]

    def test_missing_table_for_columns(self):
        resp = lambda_handler(_api_event("/columns"), None)
        assert _body(resp) == {"error": "table is required"}

    @patch("lambda_function._run_query", side_effect=TimeoutError("timed out"))
    def test_timeout_returns_200_with_error(self, _mock):
        event = _api_event("/sql-query", [
            {"name": "table", "value": "datasets"},
            {"name": "sql", "value": "SELECT * FROM {table}"},
        ])
        resp = lambda_handler(event, None)
        assert resp["response"]["httpStatusCode"] == 200
        assert "timed out" in _body(resp)["error"]

    @patch("lambda_function._run_query", side_effect=RuntimeError("boom"))
    def test_generic_exception_returns_200_with_error(self, _mock):
        event = _api_event("/sql-query", [
            {"name": "table", "value": "datasets"},
            {"name": "sql", "value": "SELECT * FROM {table}"},
        ])
        resp = lambda_handler(event, None)
        assert resp["response"]["httpStatusCode"] == 200
        assert "boom" in _body(resp)["error"]

    def test_malformed_event_does_not_crash(self):
        """A property dict missing 'name' would previously crash the Lambda."""
        event = _api_event("/sql-query", [{"wrong": "data"}])
        resp = lambda_handler(event, None)
        assert resp["response"]["httpStatusCode"] == 200
        assert "error" in _body(resp)

    def test_completely_empty_event(self):
        resp = lambda_handler({}, None)
        assert "response" in resp
        assert resp["response"]["httpStatusCode"] == 200

    def test_response_always_has_required_keys(self):
        """Even with a garbage event the response has the Bedrock-required shape."""
        resp = lambda_handler({"garbage": True}, None)
        r = resp["response"]
        assert "actionGroup" in r
        assert "httpStatusCode" in r
        assert "responseBody" in r
        # body should be valid JSON
        json.loads(r["responseBody"]["application/json"]["body"])


# ---------------------------------------------------------------------------
# _run_query network layer
# ---------------------------------------------------------------------------

class TestRunQueryNetwork:
    @patch("lambda_function._request")
    def test_start_then_poll_success(self, mock_request):
        mock_request.side_effect = [
            (200, {"token": "tok-1"}),
            (200, {"queryCount": 1, "queryResult": {"queryResults": {"headers": [], "rows": []}}}),
        ]
        from lambda_function import _run_query
        result = _run_query(TABLES["datasets"], "SELECT * FROM {table}", 25, 0x3)
        assert result["queryCount"] == 1

    @patch("lambda_function._request")
    def test_start_failure_raises(self, mock_request):
        mock_request.return_value = (400, {"reason": "bad sql"})
        from lambda_function import _run_query
        with pytest.raises(Exception, match="query start failed"):
            _run_query(TABLES["datasets"], "BAD SQL", 25, 0x3)

    @patch("lambda_function._request")
    def test_poll_failure_raises(self, mock_request):
        mock_request.side_effect = [
            (200, {"token": "tok-1"}),
            (500, {"reason": "server error"}),
        ]
        from lambda_function import _run_query
        with pytest.raises(Exception, match="query failed"):
            _run_query(TABLES["datasets"], "SELECT * FROM {table}", 25, 0x3)


# ---------------------------------------------------------------------------
# _request – HTTP layer
# ---------------------------------------------------------------------------

class TestRequest:
    @patch("lambda_function.urllib.request.urlopen")
    def test_http_error_returns_status_and_detail(self, mock_urlopen):
        err = urllib.error.HTTPError(url="http://x", code=400, msg="Bad", hdrs={}, fp=None)
        err.read = lambda: b'{"reason": "bad"}'
        mock_urlopen.side_effect = err
        from lambda_function import _request
        status, detail = _request("GET", "http://x")
        assert status == 400
        assert detail == {"reason": "bad"}

    @patch("lambda_function.urllib.request.urlopen")
    def test_socket_timeout_raises_timeout_error(self, mock_urlopen):
        mock_urlopen.side_effect = socket.timeout("timed out")
        from lambda_function import _request
        with pytest.raises(TimeoutError):
            _request("GET", "http://x")

    @patch("lambda_function.urllib.request.urlopen")
    def test_non_json_success_body_does_not_raise(self, mock_urlopen):
        """A non-JSON 200 body must not blow up json.loads — callers rely on
        always getting something .get()-able back."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda *a: None
        mock_urlopen.return_value.status = 200
        mock_urlopen.return_value.read.return_value = b"not json at all"
        from lambda_function import _request
        status, body = _request("GET", "http://x")
        assert status == 200
        assert body == {"raw": "not json at all"}

    @patch("lambda_function.urllib.request.urlopen")
    def test_http_error_non_json_body(self, mock_urlopen):
        err = urllib.error.HTTPError(url="http://x", code=502, msg="Bad Gateway", hdrs={}, fp=None)
        err.read = lambda: b"<html>gateway error</html>"
        mock_urlopen.side_effect = err
        from lambda_function import _request
        status, detail = _request("GET", "http://x")
        assert status == 502
        assert detail == {"raw": "<html>gateway error</html>"}


# ---------------------------------------------------------------------------
# _parse_response_body
# ---------------------------------------------------------------------------

class TestParseResponseBody:
    def test_valid_json(self):
        from lambda_function import _parse_response_body
        assert _parse_response_body('{"a": 1}') == {"a": 1}

    def test_empty_string(self):
        from lambda_function import _parse_response_body
        assert _parse_response_body("") == {}

    def test_invalid_json_falls_back_to_raw(self):
        from lambda_function import _parse_response_body
        assert _parse_response_body("not json") == {"raw": "not json"}


# ---------------------------------------------------------------------------
# getDatasetFiles
# ---------------------------------------------------------------------------

class TestGetDatasetFiles:
    @patch("lambda_function._request")
    def test_dataset_entity_api_path(self, mock_request):
        mock_request.return_value = (200, {
            "concreteType": "org.sagebionetworks.repo.model.table.Dataset",
            "items": [{"entityId": "syn111", "versionNumber": 1}],
            "count": 1,
            "size": 1024,
            "checksum": "abc123",
        })
        event = _api_event("/dataset-files", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["type"] == "dataset"
        assert body["items"] == [{"entityId": "syn111", "versionNumber": 1}]
        assert body["count"] == 1
        assert body["size"] == 1024
        assert body["checksum"] == "abc123"

    @patch("lambda_function._request")
    def test_container_entity_function(self, mock_request):
        mock_request.side_effect = [
            (200, {"concreteType": "org.sagebionetworks.repo.model.Folder"}),
            (200, {
                "page": [{"id": "syn222", "name": "file.txt", "type": "org.sagebionetworks.repo.model.FileEntity", "versionNumber": 1}],
                "totalChildCount": 1,
                "sumFileSizesBytes": 2048,
                "nextPageToken": "tok-1",
            }),
        ]
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["type"] == "container"
        assert body["children"] == [{
            "id": "syn222", "name": "file.txt",
            "type": "org.sagebionetworks.repo.model.FileEntity", "versionNumber": 1,
        }]
        assert body["totalChildCount"] == 1
        assert body["sumFileSizesBytes"] == 2048
        assert body["nextPageToken"] == "tok-1"
        # children request scoped to the entity as parentId
        children_call = mock_request.call_args_list[1]
        assert children_call.args[2]["parentId"] == "syn999"

    @patch("lambda_function._request")
    def test_unsupported_entity_type(self, mock_request):
        mock_request.return_value = (200, {"concreteType": "org.sagebionetworks.repo.model.Link"})
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        assert "not a Dataset or container" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_entity_fetch_failure(self, mock_request):
        mock_request.return_value = (404, {"reason": "not found"})
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        assert "Failed to fetch entity" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_non_dict_entity_response(self, mock_request):
        mock_request.return_value = (200, ["not", "a", "dict"])
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        assert "Failed to fetch entity" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_dataset_items_truncated_to_limit(self, mock_request):
        items = [{"entityId": f"syn{i}", "versionNumber": 1} for i in range(10)]
        mock_request.side_effect = [
            (200, {
                "concreteType": "org.sagebionetworks.repo.model.table.Dataset",
                "items": items,
                "count": 10,
            }),
            (200, {"restrictionInformation": []}),
        ]
        event = _function_event("getDatasetFiles", [
            {"name": "id", "value": "syn999"},
            {"name": "limit", "value": "3"},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert len(body["items"]) == 3
        assert body["returnedCount"] == 3
        assert body["count"] == 10  # true total is preserved even though truncated

    @patch("lambda_function._request")
    def test_dataset_branch_embeds_restriction(self, mock_request):
        mock_request.side_effect = [
            (200, {
                "concreteType": "org.sagebionetworks.repo.model.table.Dataset",
                "items": [], "count": 0,
            }),
            (200, {"restrictionInformation": [
                {"objectId": 999, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False},
            ]}),
        ]
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["restriction"] == {"restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False}
        # the restriction check was scoped to the dataset's own id
        restriction_call_body = mock_request.call_args_list[1].args[2]
        assert restriction_call_body["objectIds"] == ["syn999"]

    @patch("lambda_function._request")
    def test_container_forwards_next_page_token(self, mock_request):
        mock_request.side_effect = [
            (200, {"concreteType": "org.sagebionetworks.repo.model.Folder"}),
            (200, {"page": [], "totalChildCount": 0, "sumFileSizesBytes": 0}),
            (200, {"restrictionInformation": []}),
        ]
        event = _function_event("getDatasetFiles", [
            {"name": "id", "value": "syn999"},
            {"name": "nextPageToken", "value": "tok-abc"},
        ])
        lambda_handler(event, None)
        children_call_body = mock_request.call_args_list[1].args[2]
        assert children_call_body["nextPageToken"] == "tok-abc"

    @patch("lambda_function._request")
    def test_container_omits_next_page_token_when_absent(self, mock_request):
        mock_request.side_effect = [
            (200, {"concreteType": "org.sagebionetworks.repo.model.Folder"}),
            (200, {"page": [], "totalChildCount": 0, "sumFileSizesBytes": 0}),
            (200, {"restrictionInformation": []}),
        ]
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        lambda_handler(event, None)
        children_call_body = mock_request.call_args_list[1].args[2]
        assert "nextPageToken" not in children_call_body

    @patch("lambda_function._request")
    def test_non_dict_children_response(self, mock_request):
        mock_request.side_effect = [
            (200, {"concreteType": "org.sagebionetworks.repo.model.Folder"}),
            (200, "not a dict"),
        ]
        event = _function_event("getDatasetFiles", [{"name": "id", "value": "syn999"}])
        resp = lambda_handler(event, None)
        assert "Failed to list children" in _body(resp)["error"]

    def test_missing_id(self):
        resp = lambda_handler(_function_event("getDatasetFiles"), None)
        assert _body(resp) == {"error": "id is required"}


# ---------------------------------------------------------------------------
# getFileDetails
# ---------------------------------------------------------------------------

class TestGetFileDetails:
    @patch("lambda_function._request")
    def test_success_api_path(self, mock_request):
        mock_request.return_value = (200, {"list": [
            {"id": "fh1", "fileName": "a.txt", "contentSize": 10, "contentType": "text/plain", "contentMd5": "abc"},
        ]})
        event = _api_event("/file-details", [{"name": "id", "value": "syn222"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["id"] == "syn222"
        assert body["files"] == [{
            "fileHandleId": "fh1", "fileName": "a.txt",
            "contentSize": 10, "contentType": "text/plain", "contentMd5": "abc",
        }]

    @patch("lambda_function._request")
    def test_success_with_version_function(self, mock_request):
        mock_request.side_effect = [
            (200, {"list": []}),
            (200, {"restrictionInformation": [{"objectId": 222, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False}]}),
        ]
        event = _function_event("getFileDetails", [
            {"name": "id", "value": "syn222"},
            {"name": "versionNumber", "value": "3"},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["files"] == []
        assert body["restriction"] == {"restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False}
        called_url = mock_request.call_args_list[0].args[1]
        assert "/version/3/filehandles" in called_url

    @patch("lambda_function._request")
    def test_embeds_restriction_info(self, mock_request):
        mock_request.side_effect = [
            (200, {"list": [{"id": "fh1", "fileName": "a.txt", "contentSize": 10, "contentType": "text/plain", "contentMd5": "abc"}]}),
            (200, {"restrictionInformation": [{"objectId": 222, "restrictionLevel": "CONTROLLED_BY_ACT", "hasUnmetAccessRequirement": True}]}),
        ]
        event = _function_event("getFileDetails", [{"name": "id", "value": "syn222"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["restriction"] == {"restrictionLevel": "CONTROLLED_BY_ACT", "hasUnmetAccessRequirement": True}
        # restriction lookup was scoped to the same id
        restriction_call_body = mock_request.call_args_list[1].args[2]
        assert restriction_call_body["objectIds"] == ["syn222"]

    @patch("lambda_function._request")
    def test_invalid_version_number(self, mock_request):
        event = _function_event("getFileDetails", [
            {"name": "id", "value": "syn222"},
            {"name": "versionNumber", "value": "not-a-number"},
        ])
        resp = lambda_handler(event, None)
        assert "versionNumber must be an integer" in _body(resp)["error"]
        mock_request.assert_not_called()

    @patch("lambda_function._request")
    def test_fetch_failure(self, mock_request):
        mock_request.return_value = (500, {"reason": "server error"})
        event = _function_event("getFileDetails", [{"name": "id", "value": "syn222"}])
        resp = lambda_handler(event, None)
        assert "Failed to fetch file handles" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_non_dict_response_returns_clean_error(self, mock_request):
        mock_request.return_value = (200, ["unexpected", "list"])
        event = _function_event("getFileDetails", [{"name": "id", "value": "syn222"}])
        resp = lambda_handler(event, None)
        assert "Failed to fetch file handles" in _body(resp)["error"]

    def test_missing_id(self):
        resp = lambda_handler(_function_event("getFileDetails"), None)
        assert _body(resp) == {"error": "id is required"}


# ---------------------------------------------------------------------------
# checkRestriction
# ---------------------------------------------------------------------------

class TestCheckRestriction:
    @patch("lambda_function._request")
    def test_success_api_path(self, mock_request):
        # Synapse's real response keys results by a bare integer objectId
        # (confirmed against the OpenAPI spec), NOT the "synXXXXX" string
        # sent in the request — the function must translate back to "syn...".
        mock_request.return_value = (200, {"restrictionInformation": [
            {"objectId": 999, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False},
        ]})
        event = _api_event("/check-restriction", [{"name": "ids", "value": ["syn999"]}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["restrictions"]["syn999"] == {
            "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False,
        }

    @patch("lambda_function._request")
    def test_comma_separated_string_ids_function(self, mock_request):
        mock_request.return_value = (200, {"restrictionInformation": [
            {"objectId": 1, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False},
            {"objectId": 2, "restrictionLevel": "CONTROLLED_BY_ACT", "hasUnmetAccessRequirement": True},
        ]})
        event = _function_event("checkRestriction", [{"name": "ids", "value": "syn1, syn2"}])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert set(body["restrictions"]) == {"syn1", "syn2"}
        sent_body = mock_request.call_args.args[2]
        assert sent_body == {"restrictableObjectType": "ENTITY", "objectIds": ["syn1", "syn2"]}

    def test_too_many_ids(self):
        event = _function_event("checkRestriction", [
            {"name": "ids", "value": [f"syn{i}" for i in range(51)]},
        ])
        resp = lambda_handler(event, None)
        assert "at most 50 ids" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_fetch_failure(self, mock_request):
        mock_request.return_value = (500, {"reason": "server error"})
        event = _function_event("checkRestriction", [{"name": "ids", "value": ["syn999"]}])
        resp = lambda_handler(event, None)
        assert "Failed to check restriction info" in _body(resp)["error"]

    @patch("lambda_function._request")
    def test_non_dict_response_returns_clean_error(self, mock_request):
        mock_request.return_value = (200, "not a dict")
        event = _function_event("checkRestriction", [{"name": "ids", "value": ["syn999"]}])
        resp = lambda_handler(event, None)
        assert "Failed to check restriction info" in _body(resp)["error"]

    def test_missing_ids(self):
        resp = lambda_handler(_function_event("checkRestriction"), None)
        assert _body(resp) == {"error": "ids is required"}

    @patch("lambda_function._request")
    def test_bracketed_pseudo_json_ids_from_real_requestBody_event(self, mock_request):
        # Bedrock's real requestBody event delivers array-typed values as a
        # string — sometimes malformed pseudo-JSON like "[syn1, syn2]"
        # (unquoted) rather than valid JSON. Before extract_params decoded
        # this, the old comma-split fallback would corrupt it into
        # ["[syn1", "syn2]"] silently, with no error at all.
        mock_request.return_value = (200, {"restrictionInformation": [
            {"objectId": 1, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False},
            {"objectId": 2, "restrictionLevel": "OPEN", "hasUnmetAccessRequirement": False},
        ]})
        event = _api_event("/check-restriction", [
            {"name": "ids", "type": "array", "value": "[syn1, syn2]"},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert set(body["restrictions"]) == {"syn1", "syn2"}
        sent_body = mock_request.call_args.args[2]
        assert sent_body == {"restrictableObjectType": "ENTITY", "objectIds": ["syn1", "syn2"]}


# ---------------------------------------------------------------------------
# build_explore_url
# ---------------------------------------------------------------------------

class TestBuildExploreUrl:
    def test_unfiltered_url(self):
        result = build_explore_url({"table": "datasets"})
        assert result["url"].startswith("/Explore/Datasets/?qw0=")
        query = _decode_qw0(result["url"])
        assert query == {
            "sql": f"SELECT * FROM {TABLES['datasets']}",
            "includeEntityEtag": False,
            "isConsistent": True,
        }

    def test_search_expression_becomes_additional_filters(self):
        result = build_explore_url({"table": "datasets", "searchExpressions": ["glioma"]})
        query = _decode_qw0(result["url"])
        assert query["additionalFilters"] == [{
            "concreteType": "org.sagebionetworks.repo.model.table.TextMatchesQueryFilter",
            "searchExpression": "glioma",
            "searchMode": "NATURAL_LANGUAGE",
        }]
        assert "selectedFacets" not in query

    def test_multiple_search_expressions_become_independent_filters(self):
        # Each entry renders as its own separate, independently-removable
        # filter chip, distinct from merging them into one string (which
        # searches as a single phrase instead). NOTE: this only asserts the
        # *shape* built here — it does NOT mean the portal ANDs the chips
        # together. Live-testing (see build_explore_url's docstring and
        # plans/fix-searchexpressions-and-semantics.md) found multiple
        # entries behave like an OR/union on this deployment, regardless of
        # searchMode or `+` operators. Use `facets` for a true AND.
        result = build_explore_url({
            "table": "publications",
            "searchExpressions": ["glioma", "single cell RNA sequencing"],
        })
        query = _decode_qw0(result["url"])
        assert query["additionalFilters"] == [
            {
                "concreteType": "org.sagebionetworks.repo.model.table.TextMatchesQueryFilter",
                "searchExpression": "glioma",
                "searchMode": "NATURAL_LANGUAGE",
            },
            {
                "concreteType": "org.sagebionetworks.repo.model.table.TextMatchesQueryFilter",
                "searchExpression": "single cell RNA sequencing",
                "searchMode": "NATURAL_LANGUAGE",
            },
        ]

    def test_search_expressions_must_be_a_list(self):
        result = build_explore_url({"table": "datasets", "searchExpressions": "glioma"})
        assert result == {"error": "searchExpressions must be a list of strings"}

    def test_search_expressions_rejects_empty_entry(self):
        result = build_explore_url({"table": "datasets", "searchExpressions": ["glioma", ""]})
        assert result == {"error": "each searchExpressions entry must be a non-empty string"}

    def test_facets_become_selected_facets(self):
        result = build_explore_url({
            "table": "datasets",
            "facets": [{"columnName": "species", "values": ["Zebrafish"]}],
        })
        query = _decode_qw0(result["url"])
        assert query["selectedFacets"] == [{
            "concreteType": "org.sagebionetworks.repo.model.table.FacetColumnValuesRequest",
            "columnName": "species",
            "facetValues": ["Zebrafish"],
        }]
        assert "additionalFilters" not in query

    def test_multiple_facets(self):
        result = build_explore_url({
            "table": "datasets",
            "facets": [
                {"columnName": "species", "values": ["Human"]},
                {"columnName": "tumorType", "values": ["Glioma", "Glioblastoma"]},
            ],
        })
        query = _decode_qw0(result["url"])
        assert len(query["selectedFacets"]) == 2
        assert query["selectedFacets"][1]["facetValues"] == ["Glioma", "Glioblastoma"]

    def test_education_path_has_literal_space(self):
        result = build_explore_url({"table": "education"})
        assert result["url"].startswith("/Explore/Educational%20Resources/")
        assert RESOURCE_PATHS["education"] == "Educational Resources"

    def test_url_is_relative_not_absolute(self):
        # A prior version returned an absolute URL (with the https://...
        # domain), which broke the chat frontend's redirect handling — it
        # resolves <target> against its own base URL, same as every other
        # literal Collection/Detail Page target this agent uses.
        result = build_explore_url({"table": "datasets"})
        assert not result["url"].startswith("http")

    def test_missing_table(self):
        assert build_explore_url({}) == {"error": "table is required"}

    @patch("lambda_function.gzip.compress")
    def test_self_verification_catches_encoding_bug(self, mock_compress):
        # Simulate a future regression in the encode step (e.g. compressing
        # the wrong bytes) — self-verification must catch it and return an
        # error rather than a URL that silently doesn't work.
        mock_compress.return_value = _WRONG_COMPRESSED_PAYLOAD
        result = build_explore_url({"table": "datasets"})
        assert result == {"error": "internal error: qw0 self-verification mismatch"}

    def test_self_verification_passes_for_real_output(self):
        # The success-path counterpart: real output must NOT trip the check.
        result = build_explore_url({"table": "datasets", "searchExpressions": ["glioma"]})
        assert "error" not in result
        assert "url" in result

    def test_raw_synid_rejected(self):
        result = build_explore_url({"table": TABLES["datasets"]})
        assert "error" in result
        assert "datasets" in result["error"]  # names a valid alias instead

    def test_facet_missing_values_errors(self):
        result = build_explore_url({
            "table": "datasets",
            "facets": [{"columnName": "species"}],
        })
        assert result == {"error": "each facet requires columnName and values"}

    def test_via_lambda_handler(self):
        event = _function_event("buildExploreUrl", [
            {"name": "table", "value": "publications"},
            {"name": "searchExpressions", "value": ["glioma"]},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert body["url"].startswith("/Explore/Publications/?qw0=")
        query = _decode_qw0(body["url"])
        assert query["sql"] == f"SELECT * FROM {TABLES['publications']}"

    def test_search_expressions_from_real_requestBody_clean_json(self):
        # Bedrock's real requestBody event delivers array-typed values as a
        # JSON-encoded string, not a native list — this is the well-formed
        # case.
        event = _api_event("/explore-url", [
            {"name": "table", "type": "string", "value": "publications"},
            {"name": "searchExpressions", "type": "array", "value": '["glioma", "single cell RNA sequencing"]'},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert "error" not in body
        query = _decode_qw0(body["url"])
        assert [f["searchExpression"] for f in query["additionalFilters"]] == [
            "glioma", "single cell RNA sequencing",
        ]

    def test_search_expressions_from_real_requestBody_malformed_pseudo_json(self):
        # Reproduces the exact failure from a live dev-agent trace: Bedrock
        # sent searchExpressions as the literal string
        # "[breast cancer, RNA sequencing]" — bracketed but with unquoted,
        # comma-separated scalars, not valid JSON. Before extract_params
        # decoded this, build_explore_url received that raw string and
        # rejected it every retry with "searchExpressions must be a list of
        # strings," regardless of how many times the model retried with the
        # same (from its perspective, correct) input.
        event = _api_event("/explore-url", [
            {"name": "table", "type": "string", "value": "datasets"},
            {"name": "searchExpressions", "type": "array", "value": "[breast cancer, RNA sequencing]"},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert "error" not in body
        query = _decode_qw0(body["url"])
        assert [f["searchExpression"] for f in query["additionalFilters"]] == [
            "breast cancer", "RNA sequencing",
        ]

    def test_facets_from_real_requestBody_json_string(self):
        # Reproduces the second failure from the same live trace: facets
        # arrived as a syntactically-valid JSON string that was never
        # decoded, so `for facet in facets:` iterated individual characters
        # of the string and `char.get(...)` raised
        # "'str' object has no attribute 'get'".
        facets_json = json.dumps([
            {"columnName": "tumorType", "values": ["Breast Carcinoma", "Breast Adenocarcinoma"]},
            {"columnName": "assay", "values": ["RNA Sequencing", "Single Cell RNA-Sequencing"]},
        ])
        event = _api_event("/explore-url", [
            {"name": "table", "type": "string", "value": "datasets"},
            {"name": "facets", "type": "array", "value": facets_json},
        ])
        resp = lambda_handler(event, None)
        body = _body(resp)
        assert "error" not in body
        query = _decode_qw0(body["url"])
        assert query["selectedFacets"][0]["columnName"] == "tumorType"
        assert query["selectedFacets"][1]["facetValues"] == ["RNA Sequencing", "Single Cell RNA-Sequencing"]
