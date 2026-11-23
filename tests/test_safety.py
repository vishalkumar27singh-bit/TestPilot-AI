from core.safety import UnsafeTargetError, validate_url, check_availability, safe_request
from tests.helpers import LOOPBACK, base_url

BLOCKED = ["http://127.0.0.1", "http://localhost:8000", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5", "http://192.168.1.1",
           "http://[::1]/", "http://[::ffff:127.0.0.1]/", "ftp://example.com", "http://user:pw@example.com", "", "not a url", "intranet", "http://0.0.0.0"]


def _blocked(url, **kw):
    try:
        validate_url(url, **kw)
    except UnsafeTargetError:
        return True
    return False


def test_ssrf_targets_blocked_by_default():
    for u in BLOCKED:
        assert _blocked(u), f"{u!r} should be blocked"


def test_loopback_opt_in_does_not_open_private_ranges():
    assert not _blocked("http://127.0.0.1:8000", allow_loopback=True)
    assert _blocked("http://10.0.0.5", allow_loopback=True)
    assert _blocked("http://169.254.169.254", allow_loopback=True)


def test_availability_ok_and_down():
    assert check_availability(base_url() + "/index.html", **LOOPBACK)["ok"]
    assert not check_availability("http://127.0.0.1:9/", **LOOPBACK)["ok"]
    assert not check_availability("http://127.0.0.1:9/")["ok"]  # blocked w/o opt-in


def test_redirect_to_internal_address_is_blocked():
    # the sample server answers /redirect-internal with 302 -> cloud metadata IP; every hop must be re-validated
    try:
        safe_request("GET", base_url() + "/redirect-internal", **LOOPBACK)
        raise AssertionError("redirect to 169.254.169.254 was followed")
    except UnsafeTargetError:
        pass
    av = check_availability(base_url() + "/redirect-internal", **LOOPBACK)
    assert not av["ok"] and "private" in av["error"].lower()
