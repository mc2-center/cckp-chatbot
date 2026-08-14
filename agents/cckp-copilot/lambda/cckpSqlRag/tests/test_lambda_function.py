"""Unit tests for the cckpSqlRag Lambda function.

All Synapse network calls are mocked so no live token/endpoint is needed.
"""

import json
import socket
import urllib.error
from unittest.mock import patch

import pytest

from lambda_function import (
    _make_response,
    _parse_bundle,
    _resolve_table,
    extract_params,
    lambda_handler,
    TABLES,
)


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

    def test_malformed_property_raises(self):
        event = _api_event("/sql-query", [{"wrong_key": "oops"}])
        with pytest.raises(KeyError):
            extract_params(event)


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
