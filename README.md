# TikTok Live Recorder

A Windows desktop app for recording TikTok LIVE conversations, with comments,
a stable guest grid, and speaker highlights when participant data is available.

**[Download the Windows app](https://github.com/AckwellFoley/TikTok-Live-Recorder/releases/latest)** ·
[Source code](https://github.com/AckwellFoley/TikTok-Live-Recorder) ·
[Report a problem](https://github.com/AckwellFoley/TikTok-Live-Recorder/issues)

## Download, unzip, open

1. Open **Releases** above and download **`TikTok-Live-Recorder-1.0.0-Windows-x64.zip`**.
2. Right-click the ZIP and choose **Extract All**. Keep the extracted folder together.
3. Open **`TikTok Live Recorder.exe`** inside it.
4. Enter a TikTok username or full LIVE link, then click **Start recording**.
5. Click **Stop selected** when finished. Wait for saving to complete, then choose **Play latest**.

**You do not need Python, pip, FFmpeg installed system-wide, or administrator access.**
On the first recording, the app downloads FFmpeg 9.0.2 from Gyan.dev (about
110 MB), verifies its pinned SHA-256 checksum, and unpacks it into its own data
folder. The app shows progress. Later recordings reuse that download.

Requirements: **Windows 10 or 11, 64-bit**, an internet connection, free disk
space, and access to the creator's LIVE. Other operating systems are not tested
or supported by this desktop release. Do not run the app inside the ZIP preview.

GitHub's green **Code → Download ZIP** button downloads the *source*. For the
ready-to-run app, use the **Windows-x64.zip release asset** instead.

## What it records

With **Include comments & guest panels** enabled, the saved video has three
separate columns:

| Left | Centre | Right |
| --- | --- | --- |
| LIVE comments received during recording | Original video feed | Guest profiles in a stable square grid |

Each column is 720 × 1280; the combined video is **2160 × 1280**, with audio.
The original feed is saved separately too. Disabling panels saves just the feed.
New guests go to the end of the list; departing guests leave space for remaining
participants to move up. Feed thumbnails already embedded by TikTok stay in
the original video.

The app also offers:

- A saved creator list with **Check now** and optional LIVE status monitoring.
- **Monitor automatically** to wait for a creator and retry after a LIVE ends.
- Independent simultaneous recordings, with extra creators queued.
- A guest preview, recording list, activity messages, and configurable output folder.
- Speaker highlights based on TikTok's per-stream speech flags, with identity
  coverage displayed as a count such as **7/9**.

Start with **one or two recordings** and increase gradually in **Recording
options**. The limit can be set from 1 to 12; this is a queue limit, not a promise
that every computer or internet connection can handle twelve recordings.
Rendering panels can use substantial CPU and take time after capture stops.

## If TikTok requires a login

Some LIVE streams or regions require a TikTok session. The app includes a small,
auditable Chrome extension to export cookies for TikTok only.

1. In the app, click **TikTok login → Open exporter folder**.
2. In Chrome, open `chrome://extensions`, enable **Developer mode**, and choose
   **Load unpacked**. Select that `tiktok-cookie-exporter` folder.
3. Sign into TikTok normally in Chrome and check you can watch the creator's LIVE.
4. Open **TikTok Login Exporter** from Chrome's Extensions menu and click
   **Export TikTok login**.
5. In the recorder's **TikTok login** dialog, choose **Import cookies** and select
   the downloaded `tiktok-recorder-cookies.json` file.
6. Start recording again. Re-export if TikTok later expires the session.

The export contains account access information. Keep it private; never attach
it to an issue or commit it to GitHub. The extension has no background service,
analytics or uploads. Its permissions and complete source are in
[`browser-extension/tiktok-cookie-exporter`](browser-extension/tiktok-cookie-exporter).
Importing cookies does not grant access to a LIVE the account cannot watch.

## Files, settings and updates

The app creates a **`data`** folder beside the executable. By default videos go
to **`data/recordings`**; choose another location in **Recording options**.
Settings, creator history, cached FFmpeg and imported cookies also stay under
`data`. Cookies are stored locally as plaintext, protected by the user's normal
file permissions; see [Privacy](PRIVACY.md).

Keep `data` private. To update, extract a new release into a separate folder,
close the old app after saving, and copy your `data` folder across if wanted.
There is no automatic code replacement or hidden updater.

## Limits and troubleshooting

- **Missing or delayed guests/highlights:** TikTok does not always send the full
  panel identity snapshot. Explicit identities are preferred; a conservative
  score-correlation fallback helps identify guests already present when capture
  starts. Tied or conflicting totals stay unresolved. This is a reconstruction,
  not TikTok's own app interface, and highlighting is best effort.
- **Comments:** only comments delivered to the event connection during capture
  are included. Reconnection gaps can leave missing comments or guest changes.
- **Login required / access denied:** confirm browser access, then import a fresh
  export. Do not share the cookie file. A VPN, proxy, region restriction or
  TikTok rate limit can also block a request. Check your network configuration;
  the app does not change VPN settings or bypass access restrictions.
- **Video engine setup fails:** check internet access and available disk space.
  Hash mismatches are rejected. Advanced users can install FFmpeg plus ffprobe
  together and put them on PATH, or set `TIKTOK_RECORDER_FFMPEG` to the FFmpeg
  executable; ffprobe must be in the same folder. A recent FFmpeg is required.
- **Stop takes time:** wait for **Saving** / panel rendering to finish. Closing or
  killing a worker during finalization can leave an incomplete video.
- **Windows security warning:** releases are currently unsigned. Check the
  release's hashes and scan report, or build from the published source. Do not
  disable antivirus protection to run the app.
- **Source download:** install Python 3.11+ with Tcl/Tk, then double-click
  **Run from source.cmd**. It creates a local environment and installs the pinned
  dependencies from PyPI. The release ZIP is easier for non-developers.

TikTok changes its services frequently. Availability, comments, guest membership
and authentication may change without notice. This app is unofficial and is not
affiliated with TikTok. Record only content you have permission to save and
respect applicable laws, creator rights and TikTok's terms.

## Transparency and security

The release includes the human-readable application source in `source/`.
There is no UPX packing, custom obfuscation, telemetry, auto-start registration,
screen capture, microphone recording or automatic upload in the desktop app.
It downloads the LIVE media stream; it does not record the desktop.

See [Security](SECURITY.md) for the release checks and their limits,
[Privacy](PRIVACY.md) for network destinations and local data, and
[third-party notices](THIRD-PARTY-NOTICES.md) for dependency licenses.
Each release provides **SHA256SUMS.txt** and a security report tied to its exact
ZIP hash. A successful scan is evidence from that scan, not a guarantee of safety.

## Build and test

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/build_release.py
```

The build creates the Windows ZIP and separate source ZIP in `release-artifacts`.
Build on Windows x64; PyInstaller does not cross-compile this release. The
packaging specification, pinned dependencies and tests are all public. Builds
are repeatable from these instructions but are **not claimed byte-for-byte
reproducible** across machines. See [Contributing](CONTRIBUTING.md).

## Credits and license

MIT license. Based on [Michele0303/tiktok-live-recorder](https://github.com/Michele0303/tiktok-live-recorder),
with the original copyright and license retained. This fork adds the desktop
GUI, process-isolated concurrent recording, creator monitoring, reconstructed
guest/comment panels, speaker identity handling and portable packaging.

TikTok events use [TikTokLive](https://github.com/isaackogan/TikTokLive) and
[TikTokLiveProto](https://github.com/isaackogan/TikTok-Webcast-Protobuf).
Video processing uses [FFmpeg](https://ffmpeg.org), downloaded separately from
[Gyan.dev](https://www.gyan.dev/ffmpeg/builds/) under that build's GPLv3 license.
Dependency licenses are separate from this application's MIT license.
