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

Bug reports should include app version, Windows version, a description and
redacted errors. Exclude cookies, signed URLs, creator history and private media.
