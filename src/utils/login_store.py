"""Local TikTok login import. Never log or include cookie values in errors."""

import json
import re
from pathlib import Path


SESSION_NAMES = ("sessionid_ss", "sessionid", "sid_tt")
MAX_FILE_BYTES = 1024 * 1024


def has_login(cookies):
    return any(isinstance(cookies.get(name), str) and cookies[name].strip()
               for name in SESSION_NAMES)


def _validated_cookie(name, value):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
        raise ValueError("The export contains an invalid cookie name. Export it again from Chrome.")
    if not isinstance(value, str) or len(value) > 8192 or not re.fullmatch(
            r"[\x20-\x3A\x3C-\x7E]*", value):
        raise ValueError("The export contains an invalid cookie value. Export it again from Chrome.")
    return name, value


def _read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("This cookie file is too large. Choose a TikTok-only export.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("This is not a valid cookie JSON file. Export it again from Chrome.") from exc


def load_login_settings(root_dir):
    """Local imports override the upstream cookie template without editing it."""
    folder = Path(root_dir) / "src"
    path = folder / "cookies.local.json"
    if not path.is_file():
        path = folder / "cookies.json"
    if not path.is_file():
        return {}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("TikTok login settings are invalid. Import your cookies again.")
    return dict(_validated_cookie(key, value) for key, value in data.items())


def parse_cookie_export(data):
    if isinstance(data, dict):
        if (data.get("format") != "tiktok-recorder-cookies" or data.get("version") != 1
                or data.get("site") != "https://www.tiktok.com"):
            raise ValueError("Choose the JSON file saved by the TikTok Login Exporter.")
        data = data.get("cookies")
    if not isinstance(data, list) or len(data) > 500:
        raise ValueError("Choose a TikTok-only cookie export JSON file.")
    result, ranks = {}, {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("The cookie export is invalid. Export it again from Chrome.")
        domain = item.get("domain")
        if not isinstance(domain, str):
            raise ValueError("The export must identify each cookie's TikTok domain.")
        domain = domain.lower().lstrip(".")
        if domain != "tiktok.com" and not domain.endswith(".tiktok.com"):
            raise ValueError("This file contains cookies from another site. Export TikTok only.")
        # Only cookies actually applicable to the TikTok page, not other paths
        # or subdomains that might appear in a general-purpose export.
        if domain not in ("tiktok.com", "www.tiktok.com") or item.get("path", "/") != "/":
            continue
        name, value = _validated_cookie(item.get("name"), item.get("value"))
        rank = 2 if domain == "www.tiktok.com" else 1
        if value and rank > ranks.get(name, 0):
            result[name], ranks[name] = value, rank
    if not has_login(result):
        raise ValueError("No TikTok login was found. Sign into TikTok in Chrome, then export again.")
    return result


def read_cookie_export(path):
    return parse_cookie_export(_read_json(path))


def save_login_settings(root_dir, cookies):
    validated = dict(_validated_cookie(k, v) for k, v in cookies.items())
    if not has_login(validated):
        raise ValueError("Add a TikTok session cookie or import your Chrome export.")
    path = Path(root_dir) / "src" / "cookies.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(validated, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
