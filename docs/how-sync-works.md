# How the sync works

What `src/sync_sc_playlists.py` does for each playlist, and why.

- [The archive](#the-archive)
- [The quick check](#the-quick-check)
- [Dead links](#dead-links)
- [New share links](#new-share-links)
- [Renamed playlists](#renamed-playlists)
- [Removed tracks](#removed-tracks)
- [The rest of a run](#the-rest-of-a-run)

## The archive

Each playlist has an archive in `archive_trackers/sc/`, named after its link: `<user>_<playlist>.txt`, or `<user>_<playlist>_s-<token>.txt` for a secret link. One line per downloaded track:

```text
soundcloud <track_id> <file>
```

The file path is relative to the repo folder, so the repo can move. (Older archives with absolute paths still work, and `python src/migrate_relative_archive_paths.py --write` converts them.)

The archive is what `scdl --sync` trusts: a track in it is on disk, a track not in it gets downloaded. `SyncDownloadHelper` (`scdl/patches/sync_download_archive.py`) reads it at the start and rewrites it at the end of every run.

## The quick check

A full scdl sync asks SoundCloud about every track in the playlist. Most days nothing changed, so the script first checks, without downloading anything, whether there is anything new.

It assumes new tracks are **added at the end** of the playlist (oldest = position 1, newest = position N), which is how SoundCloud playlists you curate yourself behave.

### Step 1: is this the same playlist?

```sh
yt-dlp --flat-playlist --print id --playlist-items 1:3 --quiet <url>
```

- `--flat-playlist` reads the playlist's track list from SoundCloud's API. No audio.
- `--print id` prints the track id of each entry.
- `--playlist-items 1:3` keeps the 3 oldest tracks.

(The script uses yt-dlp's Python API, not the command line, but the request is the same.)

Those 3 are the oldest tracks, so they should all be in the archive. If **none** of them are, the playlist was reshuffled or the link now points at another playlist. A manual run then does a full sync. A scheduled run reports it instead (see [scheduled-sync.md](scheduled-sync.md)).

This step also runs for new playlists and with `--force-all`, to catch dead links early.

### Step 2: is there a track after the last one I have?

```text
N = number of lines in the archive
```

New tracks, if any, are at positions N+1, N+2, ...

```sh
yt-dlp --flat-playlist --print id --playlist-items {N+1}:{N+1} --quiet <url>
```

- **Nothing** at N+1: the playlist hasn't grown. Skip it, scdl isn't run.
- **A track** at N+1: go to step 3.

### Step 3: download only the new tracks

```sh
scdl -l <url> --sync --hide-progress -o {N+1}
```

`-o N+1` starts scdl at position N+1. Because `-o` is set, `SyncDownloadHelper` marks the archived tracks it didn't see this run as "not evaluated" and keeps them, instead of taking them for removed.

That's at most 4 requests for a playlist with nothing new, against one per track for a full sync. If a check request fails, a manual run falls back to a full sync, a scheduled run counts the playlist as failed and tries again next time.

`--force-all` skips steps 2 and 3 and runs a full `scdl --sync` on every playlist.

## Dead links

A secret share link stops working when the token is reset, or a public playlist turns private. SoundCloud then answers 404, or an empty playlist.

Both are caught in step 1: the playlist is reported as a dead link with its line number, and scdl isn't run. Open the playlist on SoundCloud, **Share**, copy the link and replace that line. Only the `s-<token>` part matters, `?si=...` doesn't.

If scdl does run and sees **none** of the archived tracks, `SyncDownloadHelper` also assumes a dead link rather than a playlist that was emptied: it keeps the archive, renames nothing, and logs `Stale playlist URL?`, which the script reports the same way.

## New share links

The archive name contains the token, so a fresh share link would start a new, empty archive and download everything again. Before syncing, `_carry_over_archive()` looks for an unused, non-empty archive with the same `<user>_<playlist>` and renames it (with its `.failed` and `.premium` files) to the new name. Same when a public playlist becomes private.

## Renamed playlists

Files land in a folder named after the playlist's title. When `SyncDownloadHelper` sees the title changed, it moves the whole folder's files to the new name and updates the archive paths. Files you added yourself move with them.

## Removed tracks

A track that's in the archive but no longer in the playlist is renamed `[unsync] <name>` and dropped from the archive. The file stays on disk.

This only happens on a full sync: with `--force-all`, or a manual run that falls back to one. Never on the quick check, and never on a scheduled run. Files that aren't in an archive are never touched.

## The rest of a run

- **One run at a time.** Every run takes an OS lock on `.sync.lock`. A second run exits with code 75.
- **Resume.** After each playlist, progress is saved to `.sync-state.json`. `--resume` continues from there.
- **Error filter.** scdl's output is filtered to new downloads, errors and warnings. After `--max-errors` errors in a row the playlist is dropped and the run cools down 2 minutes. Any normal `[soundcloud]` line resets the count, so scattered errors don't trigger it.
- **Pauses.** `--delay` seconds between playlists (default 8), randomized ±50%.
- **Clean-up.** At the end, every empty file in `archive_trackers/sc` is deleted. A clean run leaves an empty `.failed` behind, and an empty `.txt` means the same as no archive.
- **YouTube retry.** A manual run then runs `retry_failed_with_ytdl.py` on the failed playlists that have `[FAIL]` tracks.
- **scdl lookup.** The script runs the `scdl` next to its own Python, so the venv doesn't need to be active, from the repo folder (scdl puts archives under `<current folder>/archive_trackers/sc`).
