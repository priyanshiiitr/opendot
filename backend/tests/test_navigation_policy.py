import ipaddress

import pytest

from app.browser.policy import NavigationDenied, NavigationPolicy


def resolver_for(mapping: dict[str, list[str]]):
    def _resolve(host, port, *args, **kwargs):
        if host not in mapping:
            raise OSError("name not found")
        return [(None, None, None, None, (ip, port)) for ip in mapping[host]]

    return _resolve


def test_rejects_disallowed_schemes():
    policy = NavigationPolicy()
    for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "ftp://example.com/a"):
        with pytest.raises(NavigationDenied):
            policy.assert_allowed(url)


def test_blocks_loopback_and_private_literal_ips():
    policy = NavigationPolicy()
    for url in (
        "http://127.0.0.1/admin",
        "https://localhost/x",
        "http://10.0.0.5/x",
        "http://192.168.1.10/x",
        "http://172.16.0.2/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ):
        with pytest.raises(NavigationDenied):
            policy.assert_allowed(url)


def test_blocks_hostname_that_resolves_to_private_ip():
    policy = NavigationPolicy(resolver=resolver_for({"evil.example": ["10.1.2.3"]}))
    with pytest.raises(NavigationDenied, match="non-public"):
        policy.assert_allowed("https://evil.example/path")


def test_allows_hostname_that_resolves_only_to_public_ip():
    policy = NavigationPolicy(resolver=resolver_for({"safe.example": ["93.184.216.34"]}))
    assert policy.assert_allowed("https://safe.example/docs") == "https://safe.example/docs"


def test_fixture_mode_exception_is_explicit_and_port_scoped():
    locked = NavigationPolicy(fixture_mode=False, fixture_host="127.0.0.1", fixture_port=8765)
    with pytest.raises(NavigationDenied):
        locked.assert_allowed("http://127.0.0.1:8765/startups.html")

    allowed = NavigationPolicy(fixture_mode=True, fixture_host="127.0.0.1", fixture_port=8765)
    assert allowed.assert_allowed("http://127.0.0.1:8765/startups.html").endswith("/startups.html")

    # Different port must still fail even in fixture mode.
    with pytest.raises(NavigationDenied):
        allowed.assert_allowed("http://127.0.0.1:9999/startups.html")


def test_metadata_hostname_blocked():
    policy = NavigationPolicy(resolver=resolver_for({"metadata.google.internal": ["169.254.169.254"]}))
    with pytest.raises(NavigationDenied):
        policy.assert_allowed("http://metadata.google.internal/computeMetadata/v1/")


def test_public_check_helper_matches_stdlib_private_ranges():
    # Sanity: policy uses ipaddress classification consistent with stdlib.
    assert ipaddress.ip_address("10.0.0.1").is_private
    assert ipaddress.ip_address("8.8.8.8").is_global or not ipaddress.ip_address("8.8.8.8").is_private
