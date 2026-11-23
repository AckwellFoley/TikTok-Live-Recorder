# Changelog

Updated automatically from published GitHub release notes, newest first.

<!-- BEGIN AUTOMATED RELEASE NOTES -->

## 1.3.0 — 2026-11-23

[Release notes and downloads](https://github.com/AckwellFoley/TikTok-Live-Recorder/releases/tag/v1.3.0)

- Click creator-table headings to sort by creator name, LIVE status, recording state, elapsed time or last check; click again to reverse. Sorting stays applied as status updates arrive.
- Added a Show online only pill switch alongside creator search and All/LIVE/Active filters. Filtering changes only the view and never changes saved creators or Automatic watches.
- Added recording-library search, saved timestamps and a scrollbar. The library shows up to 500 completed MP4s and preserves selection during refresh. Enter plays the selected recording; Play latest ignores search filters.
- Made Separate clips the default for fresh settings while preserving existing saved output choices. Each separate recording saves its selected Feed, Guests and Comments clips in its own TK_creator_date-time folder. Combined mode remains one file.
- The library recognizes both older flat recordings and new session folders and keeps unfinished/recovery captures hidden.
- Added visible save-folder/free-space information. Disk checks run in the background; low space is highlighted without interrupting capture.
- Clarified the Automatic action as Watch all creators, including safely rearming saved creators after Stop all.
- Unified the rounded style of mode, layout and filter choices and identified the build version in the footer.
- This live release intentionally excludes creator statistics, statistics history, CSV statistics exports and background guest observers for unrecorded creators. Guest panels in actual recordings remain available as before.

## 1.2.0 — 2026-11-17

[Release notes and downloads](https://github.com/AckwellFoley/TikTok-Live-Recorder/releases/tag/v1.2.0)

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

## 1.1.0 — 2026-11-16

[Release notes and downloads](https://github.com/AckwellFoley/TikTok-Live-Recorder/releases/tag/v1.1.0)

- Pearl glass-style interface with rounded controls and equal-height tabs.
- Native Windows DPI scaling for sharp text and controls on 4K displays. This does not change video resolution.
- Feed always included; independent **Guests + speaker highlights** and **Comments** checkboxes.
- **Combined video** saves one selected composition. **Separate clips** saves only the selected sections; audio stays in the feed clip. No redundant clean-feed copy in combined mode.
- **Manual / Automatic** switch, with Manual as the default and a warning that unattended recordings use disk space.
- Start and Stop stay visible beneath scrollable settings.
- Failed export preserves the capture and panel metadata in the indicated recovery folder.

## 1.0.0 — 2026-11-15

[Release notes and downloads](https://github.com/AckwellFoley/TikTok-Live-Recorder/releases/tag/v1.0.0)

- Original LIVE feed plus separate comment and guest-grid panels
- Stable guest slots and best-effort speaker highlights
- Creator monitoring, independent recording workers and a concurrency queue
- TikTok-only Chrome login exporter; no maintainer login or private data
- Human-readable application source and third-party license notices

<!-- END AUTOMATED RELEASE NOTES -->

Based on upstream tiktok-live-recorder 7.7.1 by Michele0303.
