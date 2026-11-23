from testing.api_tests import ApiConfigError, run_api_suite
from tests.helpers import LOOPBACK, base_url


def status_map(cases):
    return {c.test_id: c.status for c in cases}


def test_healthy_json_endpoint_passes():
    cases, summary = run_api_suite(base_url() + "/api/items", expected_keys=["id", "name"], settings=LOOPBACK)
    assert set(status_map(cases).values()) == {"PASS"} and summary["status"] == 200


def test_invalid_json_detected():
    cases, _ = run_api_suite(base_url() + "/api/broken", settings=LOOPBACK)
    m = status_map(cases)
    assert m["API-004"] == "FAIL" and m["API-005"] == "SKIPPED"


def test_slow_endpoint_fails_time_budget():
    cases, _ = run_api_suite(base_url() + "/api/slow", max_time_ms=500, settings=LOOPBACK)
    assert status_map(cases)["API-002"] == "FAIL"


def test_missing_expected_key_fails():
    cases, _ = run_api_suite(base_url() + "/api/items/1", expected_keys=["nope"], settings=LOOPBACK)
    assert status_map(cases)["API-005"] == "FAIL"


def _raises(**kw):
    try:
        run_api_suite(settings=LOOPBACK, **kw)
    except ApiConfigError:
        return True
    return False


def test_destructive_methods_need_confirmation_and_valid_json():
    for m in ("POST", "PUT", "DELETE"):
        assert _raises(url=base_url() + "/api/items", method=m)
    assert _raises(url=base_url() + "/api/items", method="POST", body="{bad", confirm_destructive=True)
    assert _raises(url=base_url() + "/api/items", method="PATCH")
    assert not _raises(url=base_url() + "/api/items", method="POST", body='{"a":1}', confirm_destructive=True)


def test_api_blocks_internal_targets():
    assert _raises(url="http://169.254.169.254/latest/meta-data")
