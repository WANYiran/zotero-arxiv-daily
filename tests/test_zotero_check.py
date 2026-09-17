import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("check_zotero", Path(__file__).parents[1] / "scripts/check_zotero.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def opener_for(identity, calls):
    def opener(request, timeout):
        calls.append(request)
        payload = identity if request.full_url.endswith("/keys/current") else [
            {"data": {"title": "PRIVATE TITLE", "abstractNote": "PRIVATE ABSTRACT"}},
            {"data": {"abstractNote": ""}},
        ]
        response = io.StringIO(json.dumps(payload))
        response.headers = {"Total-Results": "20"}
        return response
    return opener


def test_valid_key_reports_counts_without_private_data():
    calls = []
    identity = {"userID": 123, "access": {"user": {"library": True, "write": False}}}
    result = check.verify("123", "PRIVATE-KEY", opener_for(identity, calls))
    assert "Top-level items: 20; sampled: 2; sampled items with abstracts: 1" in result
    assert "PRIVATE" not in result
    assert all("PRIVATE-KEY" not in request.full_url for request in calls)
    assert all(request.get_header("Zotero-api-key") == "PRIVATE-KEY" for request in calls)


@pytest.mark.parametrize("identity,match", [
    ({"userID": 456}, "does not match"),
    ({"userID": 123, "access": {}}, "Allow library access"),
    ({"userID": 123, "access": {"user": {"library": True, "write": True}}}, "Allow write access"),
])
def test_invalid_access_is_rejected_before_library_read(identity, match):
    calls = []
    with pytest.raises(ValueError, match=match):
        check.verify("123", "PRIVATE-KEY", opener_for(identity, calls))
    assert len(calls) == 1
