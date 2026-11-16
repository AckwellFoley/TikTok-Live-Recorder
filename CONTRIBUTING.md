# Contributing

Use Python 3.11+ and a virtual environment. The release is built and tested on
Windows x64 with Python 3.13. Install `requirements-build.txt` for the exact build
tool/dependency versions, or install the project with its `dev` extra.

Run `python -m pytest -q` before submitting changes. FFmpeg and ffprobe must be
on PATH for video integration tests. Tk tests need an available Windows desktop.
Some tests are intentionally skipped when prerequisites are missing; read the
test summary and do not claim skipped checks passed.

Run `python tools/build_release.py` for portable packaging. In an isolated build
environment, `tools/lock_dependencies.py` regenerates dependency locks and
`tools/third_party.py` refreshes notices. Review every dependency change and run
`python -m pip_audit -r requirements-lock.txt --no-deps --disable-pip`.

Test the packaged app from a newly extracted folder with no Python or FFmpeg on
PATH. Test `recorder-worker.exe --self-test report.json` and a real worker IPC
request. Never put a real TikTok login or recording in a fixture or release.

Changes should preserve stable guest slots, correct speaker identity handling,
cookie domain scoping, safe worker shutdown and the original feed. Do not infer
identity from mixed audio or guess among guests with tied/conflicting scores.
Document changes to third-party connections and data handling in `PRIVACY.md`.

## Release changelog

Write release notes in GitHub when publishing a release. A `What changed` or
`What's Changed` heading is recommended: its contents become the changelog
entry. `Changes`, `Changelog` and `Included` are also recognized; otherwise the
full release description is used. Empty descriptions get a placeholder.

The **Sync release changelog** workflow runs when releases are published or
their notes are edited. It includes published pre-releases, excludes drafts,
and rebuilds the marked section of `CHANGELOG.md` on the default branch, newest
first, with release dates and download links. It does not change release assets.
Keep manual context outside the automated markers; changes inside are replaced
on the next sync. A rerun with unchanged notes creates no duplicate or commit.

For an initial backfill or repair, open **Actions → Sync release changelog →
Run workflow** on the default branch. The workflow uses a short-lived repository
`GITHUB_TOKEN`; only the update job has `contents: write`, and the script writes
only `CHANGELOG.md`. No personal access token or repository secret is required.
Protected branches must permit this bot update, or the workflow will report a
permission error; the script does not bypass branch protection.

Offline tests: `python -m unittest discover -s tests -p test_release_changelog.py`.
Read-only preview: `python tools/sync_release_changelog.py --repository owner/repo --preview`.

Bug reports should include app version, Windows version, a description and
redacted errors. Exclude cookies, signed URLs, creator history and private media.
