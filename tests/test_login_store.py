import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from utils.login_store import load_login_settings, parse_cookie_export, read_cookie_export, save_login_settings


def cookie(name="sessionid_ss", value="FAKE_TEST_SESSION", domain=".tiktok.com", path="/"):
    return dict(name=name, value=value, domain=domain, path=path, secure=True)


def exported(*cookies):
    return dict(format="tiktok-recorder-cookies", version=1,
                site="https://www.tiktok.com", cookies=list(cookies))


def test_chrome_export_import_preserves_region_and_selects_cookie_for_page():
    data = exported(cookie(), cookie(value="FAKE_HOST_SESSION", domain="www.tiktok.com"),
                    cookie("tt-target-idc", "eu-test"), cookie("ignored", "x", path="/other"))
    assert parse_cookie_export(data) == {"sessionid_ss": "FAKE_HOST_SESSION", "tt-target-idc": "eu-test"}


@pytest.mark.parametrize("domain", ["example.com", "tiktok.com.example.com", "nottiktok.com"])
def test_import_rejects_other_sites_without_echoing_values(domain):
    with pytest.raises(ValueError) as error:
        parse_cookie_export(exported(cookie(domain=domain)))
    assert "FAKE_TEST_SESSION" not in str(error.value)


def test_login_is_required_before_saving_and_bad_import_keeps_working_settings(tmp_path):
    save_login_settings(tmp_path, {"sessionid": "FAKE_WORKING_SESSION"})
    with pytest.raises(ValueError):
        save_login_settings(tmp_path, {"tt-target-idc": "test"})
    assert load_login_settings(tmp_path) == {"sessionid": "FAKE_WORKING_SESSION"}
    for data in (exported(cookie(value="")), exported(cookie("tt-target-idc", "test"))):
        with pytest.raises(ValueError, match="No TikTok login"):
            parse_cookie_export(data)


@pytest.mark.parametrize("value", ["FAKE\nSECRET", "FAKE;SECRET", "FAKE\x00SECRET"])
def test_malformed_cookie_never_appears_in_error(value):
    with pytest.raises(ValueError) as error:
        parse_cookie_export(exported(cookie(value=value)))
    assert "FAKE" not in str(error.value)
    assert "SECRET" not in str(error.value)


def test_local_import_replaces_stale_legacy_login_without_editing_template(tmp_path):
    folder = tmp_path / "src"
    folder.mkdir()
    original = json.dumps({"sessionid_ss": "FAKE_OLD_SESSION", "tt-target-idc": "old"})
    (folder / "cookies.json").write_text(original, encoding="utf-8")
    assert load_login_settings(tmp_path)["sessionid_ss"] == "FAKE_OLD_SESSION"
    save_login_settings(tmp_path, {"sessionid": "FAKE_NEW_SESSION", "tt-target-idc": "new"})
    assert load_login_settings(tmp_path) == {"sessionid": "FAKE_NEW_SESSION", "tt-target-idc": "new"}
    assert (folder / "cookies.json").read_text(encoding="utf-8") == original
    assert not (folder / "cookies.local.json.tmp").exists()


def test_import_bounds_file_size_and_reports_invalid_json_without_contents(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("FAKE_SECRET invalid JSON", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        read_cookie_export(path)
    assert "FAKE_SECRET" not in str(error.value)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        read_cookie_export(path)


def test_import_accepts_only_identified_export_schema():
    with pytest.raises(ValueError):
        parse_cookie_export({"sessionid": "FAKE_TEST_SESSION"})
