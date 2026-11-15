"""Cookie routing checks using sentinel values and real jars, without network."""

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request as URLRequest

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from http_utils.http_client import HttpClient


SENTINEL = "fake-session-for-cookie-routing-test"


def curl_cookie_header(client, url):
    request = URLRequest(url)
    client.req.cookies.jar.add_cookie_header(request)
    return request.get_header("Cookie") or ""


def stream_cookie_header(client, url):
    request = client.req_stream.prepare_request(requests.Request("GET", url))
    return request.headers.get("Cookie", "")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: False)
    instance = HttpClient(cookies={"sessionid_ss": SENTINEL, "tt-target-idc": "fake-idc"})
    yield instance
    instance.req.close()
    instance.req_stream.close()


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/", "https://webcast.tiktok.com/webcast/room/info/", "https://tiktok.com/",
])
def test_login_cookie_is_available_to_https_tiktok_only(client, url):
    assert SENTINEL in curl_cookie_header(client, url)
    assert SENTINEL in stream_cookie_header(client, url)


@pytest.mark.parametrize("url", [
    "https://tikrec.com/tiktok/room/api/sign", "https://tiktok.eulerstream.com/webcast/room_info",
    "https://p16-sign.tiktokcdn.com/stream", "https://p16-sign.tiktokcdn-us.com/stream",
    "https://tiktok.com.evil.example/", "https://evil-tiktok.com/",
    "http://www.tiktok.com/", "http://webcast.tiktok.com/", "http://tiktok.com/",
])
def test_login_cookie_is_not_available_to_services_cdns_or_http(client, url):
    assert SENTINEL not in curl_cookie_header(client, url)
    assert SENTINEL not in stream_cookie_header(client, url)


def test_actual_curl_engine_cookie_entries_keep_domain_and_secure_flags(client):
    # curl_cffi passes its entire jar to libcurl. Domain and secure flags in
    # each CurlMorsel drive libcurl's routing, even when the requested host is
    # a third-party service. Neither field may be left blank or false.
    morsels = client.req.cookies.get_cookies_for_curl(SimpleNamespace(url="https://tikrec.com/"))
    session = next(cookie for cookie in morsels if cookie.name == "sessionid_ss")
    assert session.hostname == ".tiktok.com"
    assert session.subdomains is True
    assert session.secure is True
    fields = session.to_curl_format().split("\t")
    assert fields[:4] == [".tiktok.com", "TRUE", "/", "TRUE"]


def test_both_jars_contain_separate_secure_cookie_objects(client):
    curl_cookie = next(c for c in client.req.cookies.jar if c.name == "sessionid_ss")
    stream_cookie = next(c for c in client.req_stream.cookies if c.name == "sessionid_ss")
    assert curl_cookie is not stream_cookie
    assert curl_cookie.domain == stream_cookie.domain == ".tiktok.com"
    assert curl_cookie.secure and stream_cookie.secure


def test_host_prefix_cannot_turn_configured_cookie_into_supercookie(monkeypatch):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: False)
    client = HttpClient(cookies={"__Host-login": SENTINEL})
    try:
        assert SENTINEL not in curl_cookie_header(client, "https://tikrec.com/")
        assert all(cookie.domain == ".tiktok.com" and cookie.secure for cookie in client.req.cookies.jar)
    finally:
        client.req.close()
        client.req_stream.close()


def test_empty_or_malformed_config_values_are_not_installed(monkeypatch):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: False)
    client = HttpClient(cookies={"sessionid_ss": "", "bad\nname": "bad", "badvalue": "bad\r\nvalue",
                                 "non-string": False})
    try:
        assert list(client.req.cookies.jar) == []
        assert list(client.req_stream.cookies) == []
    finally:
        client.req.close()
        client.req_stream.close()


def test_termux_requests_only_path_remains_scoped(monkeypatch):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: True)
    client = HttpClient(cookies={"sessionid_ss": SENTINEL})
    try:
        assert client.req is client.req_stream
        assert SENTINEL in stream_cookie_header(client, "https://www.tiktok.com/")
        assert SENTINEL not in stream_cookie_header(client, "https://tikrec.com/")
        assert SENTINEL not in stream_cookie_header(client, "http://www.tiktok.com/")
    finally:
        client.req_stream.close()


def test_proxy_configuration_unchanged_and_probe_has_no_cookie(monkeypatch):
    monkeypatch.setattr("http_utils.http_client.is_termux", lambda: False)
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr("http_utils.http_client.requests.get", fake_get)
    proxy = "http://127.0.0.1:9999"
    client = HttpClient(proxy=proxy, cookies={"sessionid_ss": SENTINEL})
    try:
        assert client.req.proxies == {"http": proxy, "https": proxy}
        assert len(calls) == 1
        assert "cookies" not in calls[0][1]
        assert "headers" not in calls[0][1]
    finally:
        client.req.close()
        client.req_stream.close()
