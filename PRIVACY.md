# Privacy and network behaviour

The desktop app keeps recordings, settings, creator history, logs and imported
TikTok cookies locally. It has no analytics, crash-report upload, desktop
capture, microphone input, auto-start entry or automatic recording upload.
The optional legacy Telegram CLI module is source-only and requires explicit
configuration; the desktop release does not include or invoke it.

## Connections

- `www.tiktok.com`, TikTok webcast endpoints, TikTok WebSockets and media CDNs:
  creator/room lookup, authentication checks, media, comments and panel events.
- Public avatar CDNs: participant profile pictures, requested without cookies.
- `tikrec.com`: the inherited room lookup signing helper. Its requests receive
  ordinary creator lookup parameters, not the configured TikTok cookie jar.
- TikTokLive/Euler signing services, including `tiktok.eulerstream.com` and
  signing endpoints configured by the pinned TikTokLive dependency: anonymous
  event-connection setup, room identifiers and normal connection metadata.
- `www.gyan.dev`: one-time, pinned FFmpeg download if no engine is available.
  The complete archive SHA-256 is checked before any executable is extracted.

Normal internet requests reveal the requesting IP and user-agent to the service
receiving them. TikTok may associate an authenticated request with its account.
The app cannot control third-party service retention or TikTok's tracking.

Configured login cookies are installed as secure `.tiktok.com` domain cookies
in the HTTP clients. They are not given to media/avatars on other domains or to
the third-party signing helper. The TikTokLive event client is anonymous.
See `src/http_utils/http_client.py` and its cookie-scope regression tests.

## Local login files

The Chrome exporter reads applicable TikTok cookies only after its Export
button is pressed. It creates a local JSON download and makes no network upload.
The recorder validates the format and imports it under
`data/src/cookies.local.json`. The file is **plaintext**, not encrypted by this
application. Anyone with access to that file may be able to use the session.
Keep the `data` folder and exported JSON private. Remove unneeded exports using
your file manager, and use TikTok's normal session management to revoke access.

Recordings can contain other people's voices, profiles and comments. Logs may
contain creator names, room IDs, local filenames and error details. Inspect them
before sharing. None of this personal data belongs in a public bug report.

The repository and release archives contain no maintainer cookies, account
sessions, creator watch list, recordings, private logs or configuration files.
