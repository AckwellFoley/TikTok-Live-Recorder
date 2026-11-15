# Security

## Verify a release

Download only assets attached to a release in this repository. Compare a ZIP's
hash with that release's `SHA256SUMS.txt`, for example:

```powershell
Get-FileHash .\TikTok-Live-Recorder-1.0.0-Windows-x64.zip -Algorithm SHA256
```

The release security report records the exact artifact hash, antivirus engine,
signature version, scan date and result. Reports distinguish a completed scan
from a failed or unavailable scanner. A zero-detection result is not proof that
software is harmless or that future definitions will agree.

The executable is currently **unsigned**. No Microsoft, GitHub or antivirus
vendor certification is claimed. The project does not ask users to disable
antivirus, add exclusions or bypass browser security warnings.

## Release checks

- Regression tests, including worker isolation, cookie domain scoping, redaction,
  panel membership, speaker ambiguity and FFmpeg download hash rejection.
- A fresh dependency resolution lock and `pip-audit` report for shipped runtime
  packages. It checks known advisories, not unknown vulnerabilities.
- Source and archive checks excluding credentials, recordings, private data and
  developer-specific absolute paths.
- Offline smoke tests of the packaged GUI runtime and companion worker.
- Antivirus scanning of the extracted portable app and its ZIP, with the actual
  scan result and hashes published separately.
- Public build instructions, dependency inventory, source and license notices.

FFmpeg is downloaded separately on first use from the upstream distributor.
The app pins its version, HTTPS URL and SHA-256. It does not execute downloaded
Python or update its own code. Local and CI scans of the app ZIP do not, by
themselves, scan that later FFmpeg download; any engine scan is reported
separately.

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** option if available.
Otherwise open an issue requesting a private contact route without including
exploit details or secrets. Never post cookies, access tokens or private
recordings. This is a community project without a guaranteed response SLA.
