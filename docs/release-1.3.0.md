## What changed

- Click creator-table headings to sort by creator name, LIVE status, recording state, elapsed time or last check; click again to reverse. Sorting stays applied as status updates arrive.
- Added a Show online only pill switch alongside creator search and All/LIVE/Active filters. Filtering changes only the view and never changes saved creators or Automatic watches.
- Added recording-library search, saved timestamps and a scrollbar. The library shows up to 500 completed MP4s and preserves selection during refresh. Enter plays the selected recording; Play latest ignores search filters.
- Made Separate clips the default for fresh settings while preserving existing saved output choices. Each separate recording saves its selected Feed, Guests and Comments clips in its own TK_creator_date-time folder. Combined mode remains one file.
- The library recognizes both older flat recordings and new session folders and keeps unfinished/recovery captures hidden.
- Added visible save-folder/free-space information. Disk checks run in the background; low space is highlighted without interrupting capture.
- Clarified the Automatic action as Watch all creators, including safely rearming saved creators after Stop all.
- Unified the rounded style of mode, layout and filter choices and identified the build version in the footer.
- This live release intentionally excludes creator statistics, statistics history, CSV statistics exports and background guest observers for unrecorded creators. Guest panels in actual recordings remain available as before.

## Download and update

Download TikTok-Live-Recorder-1.3.0-Windows-x64.zip, extract the complete folder and open TikTok Live Recorder.exe. No Python installation is required. Finish all recordings and saving in the old app before transferring its private data folder. Do not share that folder or exported login cookies. Existing creator-stat files, if present in copied private data, are left untouched and are not read by this version.

## Verification

The source and Windows ZIPs, SHA256SUMS.txt and RELEASE-SECURITY.json describe this exact build. The security report records tests, archive contents checks, offline packaged GUI/worker checks, dependency audit and actual Microsoft Defender scan results. Executables remain unsigned. Guest membership and speaker identity in recordings remain best effort from TikTok data.
