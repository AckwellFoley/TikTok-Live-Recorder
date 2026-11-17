## What changed

- Fixed flickering controls by avoiding redundant status-loop redraws and variable writes.
- Added persistent light and dark themes with matching tables, dialogs, scrollbars and rounded button backgrounds.
- Replaced binary checkboxes with consistent pill switches. Added visible borders and inset thumbs so light-mode controls stay distinct in both positions, including disabled Feed.
- Simplified the title to TikTok Live Recorder and renamed the setup section Add creators.
- Removed the unused Guests preview tab; the main tabs are Creators, Recordings and Activity Log. Guest recording and speaker highlights remain available independently of comments.
- Default simultaneous recordings is now two. Existing saved limits are preserved.
- Selecting Automatic watches the full saved creator list, immediately records currently LIVE creators and arms newly added creators. Extra recordings queue at the chosen limit. Switching back to Manual disarms automatic watches without stopping current captures.
- Added a pulsing red dot while captures are actively recording.
- Added a prominent saving banner with the current creator, preparation/encoding/finishing stages, measured per-stage percentage and an animated indicator for unmeasured work. Progress also appears in the creator table without flooding Activity Log.
- Closing waits safely for saving. If saving fails, the app stays open with the recovery error instead of silently closing.
- Avoided a redundant full-feed copy where hard links are supported, with a copy fallback elsewhere. Panel composition still requires encoding time.
- Stabilized GUI test fixtures and checked native Windows DPI scaling.

## Download and update

Download TikTok-Live-Recorder-1.2.0-Windows-x64.zip, extract the complete folder and open TikTok Live Recorder.exe. No Python installation is required. Finish all recordings and saving in the previous app before transferring your private data folder. Do not share that folder or exported login cookies.

## Verification

The release includes the exact readable source, SHA256SUMS.txt and RELEASE-SECURITY.json. The security report records the actual test, dependency audit, packaged smoke and Microsoft Defender results for these artifacts. The app remains unsigned. Guest and speaker identification remains best effort from TikTok event data.
