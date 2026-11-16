"""Offline checks for release content, repeatability and safe API writes."""
import base64
import unittest
from urllib.error import HTTPError

from tools.sync_release_changelog import START, END, GitHub, release_notes, render_changelog, sync

REPOSITORY = "example/recorder"
TEMPLATE = f"# Changelog\n\nManual introduction.\n\n{START}\n\nOld entries.\n\n{END}\n\nOriginal license credit.\n"


def release(tag="v1.2.0", body="## What changed\n\n- New feature\n\n## Downloads\n\nInstaller", **kw):
    return dict(id=12, tag_name=tag, body=body, published_at="2026-11-16T12:00:00Z", draft=False, prerelease=False, **kw)


class FakeGitHub:
    repository = REPOSITORY

    def __init__(self, current=TEMPLATE, releases=None, conflict=False):
        self.current = current
        self.items = [release()] if releases is None else releases
        self.calls, self.conflict, self.reads = [], conflict, 0

    def releases(self):
        return self.items

    def request(self, suffix="", payload=None):
        self.calls.append((suffix, payload))
        if not suffix:
            return {"default_branch": "main"}
        if payload is None:
            self.reads += 1
            return {"sha": f"blob-{self.reads}", "content": base64.b64encode(self.current.encode()).decode()}
        if self.conflict:
            self.conflict = False
            self.current += "Concurrent manual footer.\n"
            raise HTTPError("https://api.github.com", 409, "Conflict", {}, None)
        self.current = base64.b64decode(payload["content"]).decode()
        return {"commit": {"sha": "new-commit"}}


class ReleaseChangelogTests(unittest.TestCase):
    def test_extracts_changes_instead_of_installation_instructions(self):
        self.assertEqual(release_notes(release()["body"]), "- New feature")
        self.assertEqual(release_notes("### Included\n- Feed\n### Checks\n- Scanned"), "- Feed")
        self.assertEqual(release_notes("## What’s Changed\n- Fixed\n## Update\nUnzip"), "- Fixed")

    def test_fallback_keeps_notes_but_nests_headings_and_ignores_code_examples(self):
        body = "# Summary\nText\n```sh\n## What changed\necho hello\n```\n## Details\nMore"
        notes = release_notes(body)
        self.assertIn("### Summary", notes)
        self.assertIn("#### Details", notes)
        self.assertIn("```sh\n## What changed\necho hello\n```", notes)

    def test_empty_notes_are_visible(self):
        self.assertEqual(release_notes(None), "No release notes were provided.")

    def test_markers_in_release_text_and_unfinished_examples_cannot_break_next_sync(self):
        notes = release_notes("```text\n" + END)
        self.assertNotIn(END, notes)
        self.assertTrue(notes.endswith("\n```"))
        updated = render_changelog(TEMPLATE, [release(body="```\n" + END)], REPOSITORY)
        self.assertEqual(render_changelog(updated, [release(body="```\n" + END)], REPOSITORY), updated)

    def test_sorted_published_history_excludes_drafts_and_includes_prereleases(self):
        old = release("v1.0.0")
        old.update(id=1, published_at="2026-01-01T00:00:00Z")
        draft = release("v9.0.0")
        draft["draft"] = True
        preview = release("v1.3.0-beta.1")
        preview.update(id=13, prerelease=True, published_at="2026-11-17T00:00:00Z")
        output = render_changelog(TEMPLATE, [old, release(), draft, preview, release()], REPOSITORY)
        self.assertLess(output.index("1.3.0-beta.1"), output.index("1.2.0"))
        self.assertLess(output.index("1.2.0"), output.index("1.0.0"))
        self.assertNotIn("9.0.0", output)
        self.assertIn("Pre-release", output)
        self.assertEqual(output.count("## 1.2.0"), 1)

    def test_preserves_manual_context_and_is_repeatable(self):
        first = render_changelog(TEMPLATE, [release()], REPOSITORY)
        self.assertTrue(first.startswith(TEMPLATE.split(START)[0]))
        self.assertTrue(first.endswith(TEMPLATE.split(END)[1]))
        self.assertIn("2026-11-16", first)
        self.assertIn("https://github.com/example/recorder/releases/tag/v1.2.0", first)
        self.assertEqual(render_changelog(first, [release()], REPOSITORY), first)

    def test_edited_notes_replace_old_text_without_duplicate_versions(self):
        first = render_changelog(TEMPLATE, [release()], REPOSITORY)
        edited = render_changelog(first, [release(body="- Corrected feature")], REPOSITORY)
        self.assertNotIn("New feature", edited)
        self.assertIn("Corrected feature", edited)
        self.assertEqual(edited.count("## 1.2.0"), 1)

    def test_invalid_or_missing_markers_and_empty_release_list_preserve_history(self):
        for template in ("# Changelog", TEMPLATE + START, END + START):
            with self.subTest(template=template), self.assertRaises(ValueError):
                render_changelog(template, [release()], REPOSITORY)
        with self.assertRaises(ValueError):
            render_changelog(TEMPLATE, [], REPOSITORY)

    def test_tag_is_markdown_escaped_and_encoded_for_its_download_link(self):
        output = render_changelog(TEMPLATE, [release("preview/[test]")], REPOSITORY)
        self.assertIn(r"preview/\[test\]", output)
        self.assertIn("/tag/preview%2F%5Btest%5D", output)

    def test_sync_only_writes_changelog_with_current_blob_and_default_branch(self):
        api = FakeGitHub()
        self.assertEqual(sync(api), "new-commit")
        writes = [(path, data) for path, data in api.calls if data is not None]
        self.assertEqual(len(writes), 1)
        path, data = writes[0]
        self.assertEqual(path, "/contents/CHANGELOG.md")
        self.assertEqual(data["sha"], "blob-1")
        self.assertEqual(data["branch"], "main")

    def test_unchanged_notes_create_no_commit(self):
        api = FakeGitHub(render_changelog(TEMPLATE, [release()], REPOSITORY))
        self.assertIsNone(sync(api))
        self.assertFalse(any(data is not None for _, data in api.calls))

    def test_conflict_rereads_changelog_and_preserves_concurrent_manual_changes(self):
        api = FakeGitHub(conflict=True)
        self.assertEqual(sync(api), "new-commit")
        self.assertTrue(api.current.endswith("Concurrent manual footer.\n"))
        self.assertEqual(api.reads, 2)
        self.assertEqual(api.calls[-1][1]["sha"], "blob-2")

    def test_client_blocks_writes_to_other_paths_and_writes_without_a_token(self):
        with self.assertRaises(ValueError):
            GitHub(REPOSITORY, "dummy").request("/contents/src/gui.py", {"content": "test"})
        with self.assertRaises(ValueError):
            GitHub(REPOSITORY).request("/contents/CHANGELOG.md", {"content": "test"})
        with self.assertRaises(ValueError):
            GitHub("https://elsewhere.test/repo")

    def test_release_list_reads_beyond_first_page(self):
        api = GitHub(REPOSITORY)
        calls = []
        def request(path):
            calls.append(path)
            return [release()] * 100 if path.endswith("&page=1") else [release("v0.1.0")]
        api.request = request
        self.assertEqual(len(api.releases()), 101)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
