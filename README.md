# scdl — SoundCloud & YouTube downloader

Local fork of [scdl-org/scdl](https://github.com/scdl-org/scdl), a SoundCloud downloader that wraps `yt-dlp` and syncs with your local files.
Extended with YouTube audio/video support and bulk playlist sync.

**Requirements:** Python 3, ffmpeg

## Install

```powershell
uv sync --dev
.\.venv\Scripts\Activate.ps1
```

```python
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pipx install scdl
pipx upgrade scdl
uv sync --dev

uv sync --upgrade-package yt-dlp
```

To update the fork:
```
git merge upstream/master
fix conflicts
uv sync
ruff check scdl/
mypy
pytest
```
---

# TODO

scdl new safety instruction: every single deletion of a song must be human in the loop/approved!
because I have added a lot of songs to playlists manually + create a trackinf list of songs that are not in soundcloud playlist for each playlist







## Weekly sync commands

```sh
python src/sync_sc_playlists.py   # sync all SoundCloud playlists (also runs daily at 18:00, see CLAUDE.md > Scheduled sync)
python src/cleanup_short_tracks.py --delete   # purge any GO+ 30s snips
python src/sync_yt_playlists.py --delay 15    # sync all YouTube audio playlists
```
/!\ make sure to be logged-in in your browser and have it preferably closed 
(cookies are specified in ytdl.cfg)

---

## SoundCloud

```sh
# Sync one playlist (downloads new, marks removed as [unsync])
scdl -l https://soundcloud.com/artist/sets/playlist-name --sync

# Sync all playlists listed in src/sc-playlists-list.md
python src/sync_sc_playlists.py
python src/sync_sc_playlists.py --delay 15         # slower inter-playlist delay (default 8s ±50%)
python src/sync_sc_playlists.py --max-errors 3     # abort a playlist after N consecutive errors (default 5)
python src/sync_sc_playlists.py --force-all        # full sync every playlist (also detects removals → [unsync])
python src/sync_sc_playlists.py --resume           # resume last interrupted run
python src/sync_sc_playlists.py --verbose          # show all scdl/yt-dlp output unfiltered
python src/sync_sc_playlists.py --unattended       # scheduled-run mode: log to logs/, ntfy alert, no prompt
python src/sync_sc_playlists.py --notify-test      # send a test alert to SC_NOTIFY_URL (.env)

# Run a subset (indices = line order in sc-playlists-list.md, stable across runs)
python src/sync_sc_playlists.py --only 2 5         # run only playlists 2 and 5
python src/sync_sc_playlists.py --skip 3 7         # skip playlists 3 and 7
python src/sync_sc_playlists.py --from 4           # start from playlist 4

# One-off downloads
scdl -l https://soundcloud.com/artist/track-name           # single track
scdl -l https://soundcloud.com/artist/sets/playlist-name   # full playlist
scdl -l https://soundcloud.com/artist -a                   # all tracks + reposts
scdl -l https://soundcloud.com/artist -f                   # likes
scdl -l https://soundcloud.com/artist -t                   # uploads only
scdl me -f                                                 # your own likes (requires auth)
```

Archives auto-named and stored in `archive_trackers/sc/`.

### How the fast-path sync works

By default `sync_sc_playlists.py` avoids scanning entire playlists. SoundCloud returns newest-added tracks first, so new songs always appear at the top of a playlist. The fast-path exploits this:

Assumes new tracks are **appended to the end** of the playlist (oldest = position 1, newest = position N). This is the typical behaviour for user-curated SC playlists.

#### Step 1 — sanity check (no download)

```sh
yt-dlp --flat-playlist --print id --playlist-items 1:3 --quiet <url>
```

- `--flat-playlist` fetches the playlist index from SC's API — no audio downloaded.
- `--print id` prints only the track ID of each entry, one per line.
- `--playlist-items 1:3` limits to the 3 oldest tracks (1-based, inclusive).

These 3 IDs are checked against the local archive. Since they are the oldest songs in the playlist, they should always be in the archive. If none match, the playlist may have been replaced or radically reordered → fall back to full sync.

#### Step 2 — find the frontier

```text
N = number of lines in archive_trackers/sc/<playlist>.txt
```

Each archive line is `soundcloud <track_id> <filepath>`, so N is simply the count of downloaded tracks. New songs, if any, must be at positions N+1, N+2, … on SC.

```sh
yt-dlp --flat-playlist --print id --playlist-items {N+1}:{N+1} --quiet <url>
```

- If this returns **nothing** → playlist unchanged → **skip entirely** (no scdl call).
- If this returns **an ID** → new songs start at N+1 → proceed to Step 3.

#### Step 3 — download only new songs

```sh
scdl -l <url> --sync --hide-progress -o {N+1} ...
```

- `-o N+1` tells scdl to start at position N+1 (`--playlist-items N+1:` internally).
- `SyncDownloadHelper` sees that `-o` is set and marks all existing archive entries that weren't downloaded this run as **"not evaluated"** — so they are preserved in the archive instead of being falsely marked `[unsync]`.

Total API calls per playlist in fast-path: **≤ 4** (3 for sanity + 1 for frontier check), versus up to hundreds for a full sync.

`--force-all` disables the fast-path and runs a full `scdl --sync` for every playlist. This is the only mode that detects tracks *removed* from a playlist and marks them `[unsync]`.

Archives auto-named and stored in `archive_trackers/sc/`.

### scdl options

```text
-l [url]                        URL can be track/playlist/user
-a                              Download all tracks of user (including reposts)
-p                              Download all playlists of a user
-o [offset]                     Start from item N in playlist (starting with 1)
--force-metadata                Re-embed metadata on already-downloaded files
--sync                          Auto-managed archive: downloads new, marks removed as [unsync]


-t                              Download all uploads of a user (no reposts)
-f                              Download all favorites (likes) of a user
-C                              Download all tracks commented on by a user
-s [search_query]               Search and use the first result
-r                              Download all reposts of user
-c                              Continue if a downloaded file already exists
--download-archive [file]       Keep track of track IDs and skip already-downloaded files
--flac                          Convert lossless originals to FLAC
--original-art                  Download full-res artwork instead of 500×500 JPEG
--original-name                 Keep original filename on original-quality downloads
--no-original                   Only download mp3/m4a/opus, skip original files
--only-original                 Only download tracks with an original file available
--opus                          Prefer opus streams over mp3
--onlymp3                       Download only mp3 files
--name-format [format]          Custom filename format (use "-" to pipe to stdout)
--playlist-name-format [format] Custom filename format for playlist tracks
--overwrite                     Overwrite existing files
--strict-playlist               Abort if one track in a playlist fails
--no-playlist                   Skip playlist entries, download only tracks
--add-description               Save track description to a .txt sidecar file
--path [path]                   Custom download directory
--min-size [size]               Skip tracks smaller than size (k/m/g)
--max-size [size]               Skip tracks larger than size (k/m/g)
--no-album-tag                  Prevent shared cover art across tracks from same album
--extract-artist                Set artist tag from title (e.g. "Artist - Title" format)
--yt-dlp-args [argstring]       Forward extra args to yt-dlp
--client-id [id]                Override the SoundCloud client_id
--auth-token [token]            Override the auth token
--debug                         Verbose logging
```

---

## YouTube — Audio (m4a, 256 kbps)

```sh
# Sync one playlist
python src/ytdl.py -l https://www.youtube.com/playlist?list=PLxxx --sync

# Resume from item N (e.g. after an interruption — skips items 1 to N-1)
python src/ytdl.py -l URL --sync -o 123

# Sync all playlists listed in src/yt-playlists.md
python src/sync_yt_playlists.py
python src/sync_yt_playlists.py --delay 15
```

Files land in `playlists/yt/<playlist name>/`. Archives in `archive_trackers/yt/`.

## YouTube — Video (mp4, 360p by default)

```sh
# Batch run without playlist (fill "src\videos_bulk.txt" with all the videos urls)
.\src\dl_videos_bulk_from_txt.bat

# One video (360p)
python src/ytdl.py -l $VIDEOURL$ --video

# Sync one playlist
python src/ytdl.py -l https://www.youtube.com/playlist?list=PLxxx --sync --video

# Sync all playlists listed in src/yt-video-playlists.md
python src/sync_yt_playlists.py --video
python src/sync_yt_playlists.py --video --delay 15

# Pick another resolution for this run
python src/ytdl.py -l $VIDEOURL$ --video --res 720    # up to 720p
python src/ytdl.py -l $VIDEOURL$ --video --res best   # no cap (best available)
python src/sync_yt_playlists.py --video --res 720
```

Video is capped at **360p** to keep files small: yt-dlp fetches the 360p stream directly,
nothing is re-encoded. `--res N` picks the best stream at or below `N` (if a video has
nothing that low, the smallest one above it is used). To change the default for every run,
set `video_res` in `ytdl.cfg` (`video_res = 720`, or `video_res = best`). Videos already
downloaded keep the quality they were downloaded at.


Files land in `playlists/yt-video/<playlist name>/`. Archives in `archive_trackers/yt-video/`.

### Archive files (YouTube)

| File | Content |
| --- | --- |
| `<playlist_id>.txt` | sync archive (tracks downloaded + file paths) |
| `<playlist_id>.failed` | tracks that failed (archive_id + URL) |
| `<playlist_id>.errors.log` | raw error output, appended per run |

Removed-from-playlist tracks are renamed with `[unsync]` prefix (same as SoundCloud).

---

## Authentication

### SoundCloud auth

Find your OAuth token: log into SoundCloud → F12 → Storage → Cookies → `oauth_token`.
Format: `2-322xxx-31626xxx1-SJsONuxxxelkKD`

Add to `scdl/scdl.cfg` (or the system config, see below):

```ini
[scdl]
auth_token = 2-322xxx-...
```

Required for GO+ tracks (256 kbps AAC) and original-quality downloads.

Config file locations:

- Windows: `C:\Users\<username>\.config\scdl\scdl.cfg`
- Mac/Linux: `~/.config/scdl/scdl.cfg`
- If `XDG_CONFIG_HOME` is set: `$XDG_CONFIG_HOME/scdl/scdl.cfg`

### YouTube (cookies) (librewolf example)

yt-dlp reads browser cookies for age-restricted or private content. Set in `ytdl.cfg`:

```ini
[ytdl]
cookies_from_browser = firefox:C:\Users\$USER$\AppData\Roaming\librewolf\Profiles\xxxx.default-default
```

**Close LibreWolf (or your browser) before syncing.** Open browsers rotate session cookies
mid-download and invalidate them after ~150 items on large playlists.

Workflow:

1. Open LibreWolf → log into YouTube (refresh session)
2. **Close LibreWolf completely**
3. Run the sync

---

## GO+ / Restricted tracks (SoundCloud)

Some tracks end up ≤30s on disk (SoundCloud serves a preview snip instead of the
full track). After `--sync`, run:

```sh
python src/cleanup_short_tracks.py --delete   # remove ≤30s snips, mark them [FAIL]
python src/retry_failed_with_ytdl.py --all    # pull [FAIL] tracks from YouTube instead (all playlists) Or specify which playlist number to retry
```

`archive_trackers/sc/` holds `<playlist>.txt` (sync archive), `<playlist>.failed`
(failed tracks, tagged by reason), `<playlist>.premium` (snips skipped pre-download).

Tags in `.failed`:

| Tag | Meaning |
| --- | --- |
| `[GO+]` | Full track behind SoundCloud GO+ paywall (`policy=SNIP`) |
| `[MONETIZE]` | Ad-gated stream yt-dlp cannot negotiate (e.g. major-label uploads) |
| `[BLOCKED]` | Geo/copyright block (`policy=BLOCK`) |
| `[FAIL]` | Any other error, with raw error message appended (includes ≤30s snips) |

---

## Data layout

```text
playlists/
  sc/                 # SoundCloud audio, one subfolder per playlist
  yt/                 # YouTube audio (m4a), one subfolder per playlist
  yt-video/           # YouTube video (mp4), one subfolder per playlist

archive_trackers/
  sc/                 # per-playlist .txt, .failed, .premium
  yt/                 # per-playlist .txt, .failed, .errors.log
  yt-video/           # per-playlist .txt, .failed, .errors.log

src/
  sc-playlists-list.md      # SoundCloud playlist URLs (gitignored)
  yt-playlists.md           # YouTube audio playlist URLs (gitignored)
  yt-video-playlists.md     # YouTube video playlist URLs (gitignored)

scdl/scdl.cfg         # SC config: client_id, auth_token, path, name_format (gitignored)
ytdl.cfg              # YT config: cookies_from_browser, path, video_path, video_res (gitignored)
```

Archive line format: `soundcloud <track_id> <absolute_path>` (SC) or `youtube <video_id> <absolute_path>` (YT).
