"""Sync the marked changelog section from published releases using the GitHub API.

Release text is Markdown data, never shell input. The only writable API path is
CHANGELOG.md on the repository's default branch. No personal token is needed in
Actions; the workflow supplies its short-lived GITHUB_TOKEN.
"""
import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

START = "<!-- BEGIN AUTOMATED RELEASE NOTES -->"
END = "<!-- END AUTOMATED RELEASE NOTES -->"
CHANGELOG = "CHANGELOG.md"
CHANGE_HEADINGS = {"what changed", "what's changed", "whats changed", "changes", "changelog", "included"}


def headings(lines):
    """Locate Markdown headings, ignoring fenced examples."""
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if fence is None and match:
            yield index, len(match[1]), match[2]


def release_notes(body):
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    found = list(headings(lines))
    for position, (start, depth, title) in enumerate(found):
        if title.lower().replace("’", "'").strip() in CHANGE_HEADINGS:
            end = next((i for i, level, _ in found[position + 1:] if level <= depth), len(lines))
            lines = lines[start + 1:end]
            break
    # Nest release-body headings below the version heading, preserving code.
    depths = list(headings(lines))
    offset = max(0, 3 - min((depth for _, depth, _ in depths), default=3))
    for index, depth, title in depths:
        lines[index] = "#" * min(6, depth + offset) + " " + title
    text = "\n".join(lines).strip()
    for marker in (START, END):
        text = text.replace(marker, marker.replace("<", "&lt;").replace(">", "&gt;"))
    # An unfinished example must not swallow the next release or our end marker.
    fence = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            run = match[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
    if fence:
        text += "\n" + fence
    return text or "No release notes were provided."


def render_changelog(current, releases, repository):
    if current.count(START) != 1 or current.count(END) != 1:
        raise ValueError("CHANGELOG.md must contain exactly one pair of automated-release markers.")
    start, end = current.index(START), current.index(END)
    if end < start:
        raise ValueError("Changelog markers are out of order.")
    published = [r for r in releases if not r.get("draft") and r.get("published_at")]
    if not published:
        raise ValueError("No published releases found; leaving the changelog intact.")
    entries, seen = [], set()
    for release in sorted(published, key=lambda r: (r["published_at"], r["id"]), reverse=True):
        tag = release["tag_name"]
        if tag in seen:
            continue
        seen.add(tag)
        if not isinstance(tag, str) or not tag or any(ord(c) < 32 for c in tag):
            raise ValueError("Invalid release tag.")
        date = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00")).date()
        label = tag[1:] if re.match(r"^v\d", tag) else tag
        label = re.sub(r"([\\`*_{}\[\]<>()#!|])", r"\\\1", label)
        suffix = " · Pre-release" if release.get("prerelease") else ""
        url = f"https://github.com/{repository}/releases/tag/{quote(tag, safe='')}"
        entries.append(f"## {label} — {date}{suffix}\n\n[Release notes and downloads]({url})\n\n{release_notes(release.get('body'))}")
    return current[:start] + START + "\n\n" + "\n\n".join(entries) + "\n\n" + current[end:]


class GitHub:
    def __init__(self, repository, token=None):
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository) or repository.split("/")[1] in (".", ".."):
            raise ValueError("Use a GitHub repository in owner/name form.")
        self.repository, self.token = repository, token

    def request(self, suffix="", payload=None):
        # Neither event data nor release text can select an API host or file path.
        url = f"https://api.github.com/repos/{self.repository}{suffix}"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "release-changelog-sync"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if payload is not None:
            if not self.token or suffix != "/contents/CHANGELOG.md":
                raise ValueError("Writes are restricted to CHANGELOG.md and require an Actions token.")
            headers["Content-Type"] = "application/json"
        request = Request(url, headers=headers, data=json.dumps(payload).encode() if payload is not None else None,
                          method="PUT" if payload is not None else "GET")
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def releases(self):
        releases = []
        for page in range(1, 101):
            batch = self.request(f"/releases?per_page=100&page={page}")
            releases.extend(batch)
            if len(batch) < 100:
                return releases
        raise ValueError("Release pagination limit reached; refusing a partial changelog.")


def sync(api):
    branch = api.request()["default_branch"]
    for attempt in range(3):
        releases = api.releases()
        file = api.request("/contents/CHANGELOG.md?" + urlencode({"ref": branch}))
        current = base64.b64decode(file["content"]).decode("utf-8")
        updated = render_changelog(current, releases, api.repository)
        if current == updated:
            return None
        try:
            result = api.request("/contents/CHANGELOG.md", {
                "message": "docs: sync changelog from published releases [skip ci]",
                "content": base64.b64encode(updated.encode()).decode(), "sha": file["sha"], "branch": branch,
            })
            return result["commit"]["sha"]
        except HTTPError as exc:
            if exc.code not in (409, 422) or attempt == 2:
                raise
    raise RuntimeError("Changelog update did not complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--preview", action="store_true", help="Read-only preview using the local CHANGELOG.md")
    args = parser.parse_args()
    api = GitHub(args.repository or "", os.environ.get("GITHUB_TOKEN"))
    if args.preview:
        print(render_changelog(Path(CHANGELOG).read_text(encoding="utf-8"), api.releases(), api.repository))
    else:
        commit = sync(api)
        print("Updated CHANGELOG.md: " + commit if commit else "CHANGELOG.md is already up to date.")


if __name__ == "__main__":
    main()
