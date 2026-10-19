# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a local fork/customization of [scdl-org/scdl](https://github.com/scdl-org/scdl), a SoundCloud downloader that wraps `yt-dlp`. The upstream README describes the package well. The local additions are:

- `src/sync_sc_playlists.py` — a standalone bulk-sync script for managing many playlists at once
- `scdl/patches/sync_download_archive.py` — custom sync archive management (not in upstream)
- `scdl/scdl.cfg` — local config (local paths). **Tracked in git** (upstream ships it), so never put an `auth_token` in it without making sure it can't be pushed
- `deploy/windows/register-task.ps1` — registers the daily scheduled sync (see [Scheduled sync](#scheduled-sync))

## Keeping the repo up to date

Two independent update paths:

**yt-dlp** (pip dep — do whenever `ytdl.py` warns about staleness):

```powershell
uv sync --upgrade-package yt-dlp
```

**scdl upstream** (monthly, or when SoundCloud behaviour changes):

```powershell
git fetch upstream
git merge upstream/master
uv sync   # regenerate lock if pyproject.toml changed
```

The merge is almost always clean. The **only file that ever has conflicts is `scdl/scdl.py`** — everything else (`src/`, `scdl/patches/`, configs) is local-only and never touched by upstream. If `pyproject.toml` conflicts, the usual cause is the `yt-dlp` version pin; take upstream's version and add back `tqdm>=4.0.0` (used by `src/sync_sc_playlists.py`).

## Commands

```powershell
# Install / activate environment
uv sync --dev
.\.venv\Scripts\Activate.ps1

# Lint
ruff check scdl/
ruff format scdl/

# Type check
mypy

# Tests
pytest

# Run the downloader (single playlist)
scdl -l <url> --sync

# Sync all playlists from the list file
python src/sync_sc_playlists.py
python src/sync_sc_playlists.py --delay 15 --max-errors 3
python src/sync_sc_playlists.py --force-all        # full sync, detects removals
python src/sync_sc_playlists.py --only 2 5         # run only playlists 2 and 5
python src/sync_sc_playlists.py --skip 3           # skip playlist 3
python src/sync_sc_playlists.py --from 4           # start from playlist 4
python src/sync_sc_playlists.py --resume           # resume last interrupted run
python src/sync_sc_playlists.py --unattended       # what the scheduled task runs (log file, ntfy alert, no prompt)
python src/sync_sc_playlists.py --notify-test      # send a test ntfy alert
```

## Scheduled sync

The playlists sync automatically every day at 18:00 on the Windows laptop via Task Scheduler. It runs on the laptop rather than a VPS or CI so that the playlist links (the user's SoundCloud username and secret tokens) and the library never reach a third party: scdl runs without an `auth_token`, from the same home IP the user already browses SoundCloud from.

- **Task**: `scdl daily sync`, registered by `deploy/windows/register-task.ps1` (admin PowerShell, once; re-run to update). Runs `.venv\Scripts\python.exe src\sync_sc_playlists.py --unattended` from the repo root, hidden, as the user, even when logged out (S4U, no stored password). A missed 18:00 run starts as soon as the laptop is back (`StartWhenAvailable`); 3 h time limit; on battery too.
- **`--unattended`** (otherwise the same sync as a manual run):
  - all output goes to `logs/sc-sync_<date>_<time>.log` (newest 30 kept), headed by the resolved python / scdl / ffmpeg paths; no progress bar, no automatic YouTube retry of failed tracks (manual runs do it at the end without asking)
  - **never full-syncs a playlist that already has an archive**: `_MISMATCH` is reported as "needs a manual full sync" (with the `--force-all --only <lines>` command), `_SCAN_FAILED` as failed and retried next run. 3 scan failures in a row stop the run (offline / blocked). New playlists (empty archive) still get their first full sync.
  - keeps Windows from idle-sleeping while it runs (`SetThreadExecutionState`)
- **Alerts**: ntfy POST to `SC_NOTIFY_URL` (env var or `.env`; use a random topic of its own). Text is counts + line numbers + SoundCloud playlist titles (as scdl prints them, `Downloading playlist: <title>`) + fixed wording only — never usernames, links, slugs, track names or exception messages — because it passes through ntfy's server. Title is `SC sync: success` unless a playlist needs action (`SC sync: action needed`: stale link, needs-full-sync, a playlist that didn't sync, missing ffmpeg). The body always starts with the new-track count, broken down per playlist. Tracks that failed inside a playlist scdl got through to the end (geo-blocked etc., `RunResult.track_errors`) are listed per playlist but still count as success. Sent when there are new tracks, a stale link, a needs-full-sync playlist, missing ffmpeg, a crash, or when the set of failing playlists or of playlists with failed tracks changed since the last unattended run (`.sync-last.json`), so a track that keeps failing alerts once rather than daily. Silent when nothing changed.
- `--force-all` stays manual: removed-track detection (`[unsync]`) only happens when the user runs it and can check the result. Files the user added to playlist folders that aren't in an archive are never touched by the sync (a playlist rename moves the whole folder, those included).

```powershell
Start-ScheduledTask -TaskName 'scdl daily sync'                       # run now
Get-ScheduledTaskInfo -TaskName 'scdl daily sync' | Select-Object LastRunTime, LastTaskResult, NextRunTime
Unregister-ScheduledTask -TaskName 'scdl daily sync' -Confirm:$false  # remove
```

## Architecture

### `scdl/scdl.py`

The main entry point. `_main()` parses CLI args (docopt), loads `scdl.cfg`, then calls `download_url()`. `_build_ytdl_params()` translates scdl's CLI flags into yt-dlp API params. The output filename template is built by `_build_ytdl_output_filename()` and switched per-track at runtime by `OuttmplPP`.

### `scdl/patches/`

Custom yt-dlp post/pre-processors injected into the `YoutubeDL` instance in `download_url()`:

- `sync_download_archive.py` — `SyncDownloadHelper` manages the `--sync` archive file. It hooks into `_match_entry` to track which IDs are seen, then in `post_download()` renames removed tracks to `[unsync] <name>` and rewrites the archive. If **none** of the archived tracks were seen, it assumes a dead URL rather than an emptied playlist: it keeps the archive, skips `[unsync]`, and logs `Stale playlist URL?`, which `sync_sc_playlists.py` picks up. It also detects playlist renames and moves files accordingly.
- `mutagen_postprocessor.py` — replaces yt-dlp's built-in EmbedThumbnail/FFmpegMetadata with a mutagen-based one for better format support.
- `switch_outtmpl_preprocessor.py` (`OuttmplPP`) — switches the output template between a standalone-track format and a playlist-track format based on `info_dict["playlist"]`.
- `original_filename_preprocessor.py` — uses the SC original filename for original-quality downloads.
- `thumbnail_selection.py` — filters thumbnails to `t500x500`.
- `trim_filenames.py` — trims filenames to 240 bytes.
- `old_archive_ids.py` — backward compat shim for pre-v3 archive IDs.

### `src/sync_sc_playlists.py`

Reads playlist URLs from `src/sc-playlists-list.md` (one `https://...` URL per line). **Indices follow line order in that file** — line 1 = `--only 1`, line 15 = `--only 15`. No sorting is applied; the user controls order via the file.

For each URL:

0. **Archive carry-over** (`_carry_over_archive()`): archive names include the secret token (`<user>_<slug>_s-<token>.txt`). When the user pastes a new share link with a different token (or a public playlist became private), an unclaimed non-empty archive with the same `<user>_<slug>` is renamed to the new name, along with its `.failed`/`.premium` files.

1. **Fast-path** (`_fast_path()`): uses yt-dlp's Python API (no subprocess) to fetch track IDs. It always fetches the 3 oldest tracks (positions 1–3) first, even for new playlists and `--force-all`. A **404 or 0 tracks → stale link**: scdl is not run and the playlist is listed at the end with its line number so the user can paste a fresh share link. The `?si=` param is irrelevant; the `s-<token>` is what expires. Unless `--force-all` is passed, those IDs then serve as a playlist identity sanity check, and position N+1 (where N = archive count) is checked to see if anything new exists. If nothing at N+1 → skip entirely. If something found → run scdl with `-o N+1`. If none of the 3 oldest IDs are in the archive (`_MISMATCH`: reshuffled, or the link now points at another playlist) or a scan request errors (`_SCAN_FAILED`), a manual run falls back to a full sync; an `--unattended` run doesn't (see [Scheduled sync](#scheduled-sync)).

2. **`run_scdl()`**: runs `scdl --sync --hide-progress` as a subprocess, using the `scdl` next to `sys.executable` (no venv activation needed) and `cwd=` the repo root (scdl puts archives under `<cwd>/archive_trackers/sc`, `scdl/scdl.py`). Filters output to show only new downloads, errors, and warnings. The consecutive-error counter resets on any `[soundcloud]` processing line (not just active download progress), so scattered errors don't falsely trigger the rate-limit abort.

3. **After each playlist**: saves resume state to `.sync-state.json`. Use `--resume` to pick up where a run was interrupted.

4. **At the end** (`_finalize()`): deletes every 0-byte file in `archive_trackers/sc` via `src/cleanup_empty_archives.py`, which can also be run standalone. Empty `.failed` files are left behind by every clean run, and an empty `.txt` means the same as a missing one.

`--force-all` disables the fast-path and runs a full scdl sync for every playlist — the way to detect removed tracks (`[unsync]`). Manual runs also full-sync a playlist on `_MISMATCH`/`_SCAN_FAILED`, which can mark `[unsync]` too.

Every run (manual or scheduled) takes an OS lock on `.sync.lock`; a second run exits with code 75. Exit codes: 0 success (individual tracks may have failed, e.g. geo-blocked), 1 something needs attention (a playlist failed / stale / needs a manual full sync), 75 another sync is running.

### Data Layout

```text
archive_trackers/sc/        # per-playlist archive .txt files (format: "soundcloud <id> <filepath>")
playlists/sc/               # downloaded audio files, one subfolder per playlist
src/sc-playlists-list.md    # list of SC playlist URLs to sync (gitignored)
logs/                       # --unattended run logs, newest 30 kept (gitignored)
.env                        # SC_NOTIFY_URL for alerts (gitignored, template: .env.example)
scdl/scdl.cfg               # local config (client_id, auth_token, path, name_format)
```

### Archive File Format

Each line: `soundcloud <track_id> <path_to_file>` — written relative to the repo root (`scdl/patches/archive_paths.py`); absolute paths are still read.

The archive is the source of truth for `--sync`. `SyncDownloadHelper` reads it on startup and rewrites it after every run.

## Config

`scdl/scdl.cfg` (tracked in git, not ignored; scdl writes the generated `client_id` back into it):

```ini
[scdl]
client_id =
auth_token =
path = playlists/sc
name_format = [%(id)s] %(uploader)s - %(title)s.%(ext)s
playlist_name_format = %(playlist_index)03d. %(uploader)s - %(title)s.%(ext)s
yt_dlp_args = --match-filters duration>30
```

The `yt_dlp_args` filter (`duration>30`) skips GO+ preview tracks (≤30s).
