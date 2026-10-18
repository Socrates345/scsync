# Usage

Every command and flag. For the daily task see [scheduled-sync.md](scheduled-sync.md), for how the sync decides what to download see [how-sync-works.md](how-sync-works.md).

- [Routine](#routine)
- [SoundCloud](#soundcloud)
  - [Sync all playlists](#sync-all-playlists)
  - [One playlist, track or user](#one-playlist-track-or-user)
  - [scdl options](#scdl-options)
- [YouTube audio](#youtube-audio)
- [YouTube video](#youtube-video)
- [GO+ and failed tracks](#go-and-failed-tracks)
- [Logins](#logins)
- [Files and folders](#files-and-folders)
- [Troubleshooting](#troubleshooting)

All commands run from the repo folder with the venv active (`. .\.venv\Scripts\Activate.ps1`).

## Routine

```powershell
python src/sync_sc_playlists.py                 # all SoundCloud playlists (also runs daily at 18:00)
python src/cleanup_short_tracks.py --delete     # remove GO+ 30-second snips already on disk
python src/sync_yt_playlists.py --delay 15      # all YouTube audio playlists
```

Before a YouTube sync, log into YouTube in your browser, then **close the browser** (see [YouTube cookies](#youtube-cookies)).

## SoundCloud

### Sync all playlists

Reads `src/sc-playlists-list.md`: one link per line, lines that don't start with `http` are ignored. Playlist numbers follow the order of the links in that file (first link = 1) and stay the same from run to run.

```powershell
python src/sync_sc_playlists.py
```

| Flag | What it does |
| --- | --- |
| `--delay S` | Seconds to wait between playlists (default 8, randomized ±50%) |
| `--max-errors N` | Give up on a playlist after N track errors in a row (default 5), then cool down 2 minutes |
| `--force-all` | Full sync of every playlist instead of the quick check. The only way to find removed tracks (`[unsync]`) |
| `--only N [N ...]` | Only these playlists |
| `--skip N [N ...]` | All playlists but these |
| `--from N` | Start at playlist N |
| `--resume` | Pick up an interrupted run where it stopped (`.sync-state.json`) |
| `--verbose` | Show all scdl / yt-dlp output, unfiltered |
| `--unattended` | What the scheduled task runs: log file, phone alert, no prompt, no full sync of existing playlists. See [scheduled-sync.md](scheduled-sync.md) |
| `--notify-test` | Send a test alert to `SC_NOTIFY_URL` and exit |

Examples:

```powershell
python src/sync_sc_playlists.py --only 2 5
python src/sync_sc_playlists.py --skip 3 7
python src/sync_sc_playlists.py --force-all --only 12
```

At the end of a run it:

- tries YouTube for tracks that failed (manual runs only, see [GO+ and failed tracks](#go-and-failed-tracks))
- lists dead links by line number: open the playlist on SoundCloud, **Share**, copy the link and replace that line. The `?si=...` part doesn't matter, the `s-<token>` part does.
- lists playlists the quick check didn't recognize, with the `--force-all --only ...` command to run
- deletes empty files in `archive_trackers/sc` (also `python src/cleanup_empty_archives.py`)

Exit codes: `0` all good, `1` something needs your attention (failed, dead link, needs a full sync), `75` another sync is already running.

### One playlist, track or user

```powershell
# Sync one playlist: download what's new, rename removed tracks to [unsync] ...
scdl -l https://soundcloud.com/<user>/sets/<playlist> --sync

# One-off downloads
scdl -l https://soundcloud.com/<user>/<track>             # one track
scdl -l https://soundcloud.com/<user>/sets/<playlist>     # whole playlist
scdl -l https://soundcloud.com/<user> -a                  # all tracks and reposts
scdl -l https://soundcloud.com/<user> -f                  # likes
scdl -l https://soundcloud.com/<user> -t                  # uploads only
scdl me -f                                                # your own likes (needs a login)
```

`--sync` names its archive after the link and keeps it in `archive_trackers/sc/`.

### scdl options

```text
-l [url]                        URL can be track/playlist/user
-a                              Download all tracks of user (including reposts)
-t                              Download all uploads of a user (no reposts)
-f                              Download all favorites (likes) of a user
-C                              Download all tracks commented on by a user
-p                              Download all playlists of a user
-r                              Download all reposts of user
-s [search_query]               Search and use the first result
-o [offset]                     Start from item N in playlist (starting with 1)
-c                              Continue if a downloaded file already exists
--sync                          Auto-managed archive: downloads new, marks removed as [unsync]
--download-archive [file]       Keep track of track IDs and skip already-downloaded files
--force-metadata                Re-embed metadata on already-downloaded files
--flac                          Convert lossless originals to FLAC
--original-art                  Download full-res artwork instead of 500x500 JPEG
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

Defaults come from `scdl/scdl.cfg`:

```ini
[scdl]
client_id =
auth_token =
path = playlists/sc
name_format = [%(id)s] %(uploader)s - %(title)s.%(ext)s
playlist_name_format = %(uploader)s - %(title)s.%(ext)s
yt_dlp_args = --match-filters duration>30
```

`duration>30` skips GO+ previews whose metadata already says they are 30 seconds or shorter. scdl fills in `client_id` by itself.

## YouTube audio

m4a, 256 kbps.

```powershell
# Sync one playlist
python src/ytdl.py -l "https://www.youtube.com/playlist?list=<id>" --sync

# Resume from item N after an interruption (skips 1 to N-1)
python src/ytdl.py -l "<url>" --sync -o 123

# Sync every playlist in src/yt-playlists.md
python src/sync_yt_playlists.py
python src/sync_yt_playlists.py --delay 15
```

Files go to `playlists/yt/<playlist>/`, archives to `archive_trackers/yt/`.

`ytdl.py` flags:

| Flag | What it does |
| --- | --- |
| `-l URL` | Playlist, channel or video |
| `--sync` | Keep an archive: download what's new, rename removed videos to `[unsync] ...` |
| `-o N`, `--offset N` | Start at item N |
| `--video` | mp4 instead of m4a (see below) |
| `--res N` | Max video height (`360`, `720`, ..., or `best`) |
| `--path PATH` | Download folder |
| `--no-playlist-folder` | Don't make a subfolder per playlist |
| `--overwrite` | Overwrite existing files |
| `--cookies-from-browser BROWSER[:PROFILE]` | Use your browser's YouTube login |
| `--cookies FILE` | Netscape `cookies.txt` instead |
| `--yt-dlp-args ARGSTRING` | Forward extra args to yt-dlp |
| `--debug` | Verbose output |

`sync_yt_playlists.py` takes `--delay`, `--max-errors`, `--video`, `--res`, `--cookies-from-browser` and `--cookies`.

## YouTube video

mp4, 360p by default.

```powershell
# One video
python src/ytdl.py -l "<video url>" --video

# Many videos without a playlist: one link per line in src/videos_bulk.txt
.\src\dl_videos_bulk_from_txt.bat

# Sync one playlist
python src/ytdl.py -l "https://www.youtube.com/playlist?list=<id>" --sync --video

# Sync every playlist in src/yt-video-playlists.md
python src/sync_yt_playlists.py --video
python src/sync_yt_playlists.py --video --delay 15

# Another resolution for this run
python src/ytdl.py -l "<video url>" --video --res 720    # up to 720p
python src/ytdl.py -l "<video url>" --video --res best   # no cap
python src/sync_yt_playlists.py --video --res 720
```

Video is capped at 360p to keep files small. yt-dlp fetches that stream directly, nothing is re-encoded. `--res N` picks the best stream at or below N, or the smallest one above it if a video has nothing that low. To change the default, set `video_res` in `ytdl.cfg` (`720`, or `best`). Videos already downloaded keep their quality.

Files go to `playlists/yt-video/<playlist>/`, archives to `archive_trackers/yt-video/`.

## GO+ and failed tracks

SoundCloud sometimes serves a 30-second preview instead of the full track (GO+), or refuses a track (DRM, ad-gated, geo-blocked). New syncs catch previews and log them as `[FAIL]`. For previews downloaded before that, and to get failed tracks from YouTube:

```powershell
python src/cleanup_short_tracks.py              # dry run: list snips on disk
python src/cleanup_short_tracks.py --delete     # delete them, drop them from the archive, log them as [FAIL]
python src/retry_failed_with_ytdl.py --all      # look up every [FAIL] track on YouTube
python src/retry_failed_with_ytdl.py <playlist> # one playlist (name, part of it, or path to its .failed file)
python src/retry_failed_with_ytdl.py --all --dry-run
```

A track found on YouTube is saved in the playlist's folder as `[YT] <artist> - <title>`, added to the archive (so the sync stops retrying it) and removed from `.failed`. A manual `sync_sc_playlists.py` run does this by itself at the end for playlists with failures. `--delay S` sets the pause between tracks (default 3s).

Each playlist in `archive_trackers/sc/` has:

| File | Content |
| --- | --- |
| `<playlist>.txt` | the sync archive: every downloaded track and its file |
| `<playlist>.failed` | tracks that failed, tagged with the reason |
| `<playlist>.premium` | GO+ previews skipped before download |

Tags in `.failed`:

| Tag | Meaning |
| --- | --- |
| `[GO+]` | Full track behind the GO+ paywall (`policy=SNIP`) |
| `[MONETIZE]` | Ad-gated stream yt-dlp can't play (e.g. major-label uploads) |
| `[BLOCKED]` | Geo or copyright block (`policy=BLOCK`) |
| `[FAIL]` | Any other error, with the raw message (includes 30-second snips). The only tag the YouTube retry acts on |

## Logins

### SoundCloud

Not needed for the sync. A login only adds GO+ tracks (256 kbps AAC, with a GO+ subscription) and original-quality files.

Find your token: log into SoundCloud, press F12, **Storage**, **Cookies**, `oauth_token`. It looks like `2-322xxx-31626xxx1-SJsONuxxxelkKD`.

Put it in your **user** config, not in `scdl/scdl.cfg`: that file is tracked by git, so a token there can end up pushed.

```ini
[scdl]
auth_token = 2-322xxx-...
```

User config locations:

- Windows: `C:\Users\<you>\.config\scdl\scdl.cfg`
- macOS / Linux: `~/.config/scdl/scdl.cfg`
- With `XDG_CONFIG_HOME` set: `$XDG_CONFIG_HOME/scdl/scdl.cfg`

### YouTube cookies

yt-dlp can use your browser's YouTube login for age-restricted or private videos. Copy `ytdl.cfg.example` to `ytdl.cfg` and set the browser. For LibreWolf (Firefox-based), point at the profile:

```ini
[ytdl]
cookies_from_browser = firefox:C:\Users\<you>\AppData\Roaming\librewolf\Profiles\<profile>.default-default
```

Supported browsers: brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale.

**Close the browser before syncing.** An open browser rotates its session cookies mid-download, and on large playlists they stop working after about 150 items.

1. Open the browser and log into YouTube
2. Close it completely
3. Run the sync

`ytdl.cfg` is git-ignored. It also sets `path`, `video_path` and `video_res`.

## Files and folders

```text
playlists/
  sc/                      SoundCloud audio, one folder per playlist
  yt/                      YouTube audio (m4a), one folder per playlist
  yt-video/                YouTube video (mp4), one folder per playlist

archive_trackers/
  sc/                      per playlist: .txt, .failed, .premium
  yt/                      per playlist: .txt, .failed, .errors.log
  yt-video/                per playlist: .txt, .failed, .errors.log

src/
  sc-playlists-list.md     SoundCloud playlist links           (git-ignored)
  yt-playlists.md          YouTube audio playlist links        (git-ignored)
  yt-video-playlists.md    YouTube video playlist links        (git-ignored)
  videos_bulk.txt          video links for the bulk .bat       (git-ignored)

scdl/scdl.cfg              SoundCloud settings (tracked: no token here)
ytdl.cfg                   YouTube settings: cookies, folders, video_res (git-ignored)
.env                       SC_NOTIFY_URL for alerts (git-ignored, template: .env.example)
logs/                      logs of scheduled runs, newest 30 kept (git-ignored)
```

An archive line is `soundcloud <track_id> <file>` or `youtube <video_id> <file>`, with the file path relative to the repo folder (older absolute paths still work).

YouTube archives are named after the playlist id, with the playlist title added in front after the first download. Next to each: `.failed` (archive id and URL of failed videos) and `.errors.log` (raw errors, appended every run). Removed videos are renamed `[unsync] ...`, as on SoundCloud.

## Troubleshooting

| You see | Do |
| --- | --- |
| "STALE" / "Stale link: line N" | The secret link was reset or the playlist made private. Paste a fresh share link on that line. The old archive is picked up automatically. |
| "not recognised by the quick check" | The playlist was reshuffled or the link points at another playlist. Run the `--force-all --only N` command it prints and check what gets `[unsync]`. |
| Exit code 75 | Another sync is running (the scheduled one?). Wait for it. |
| "Rate limit — cooling down" | SoundCloud is pushing back. Raise `--delay`, or try later. |
| "yt-dlp vX is N days old" | `uv sync --upgrade-package yt-dlp` |
| YouTube sync fails after ~150 items | The browser was open. Close it and run the sync again: what's already in the archive is skipped. |
| 30-second files | `python src/cleanup_short_tracks.py --delete`, then `python src/retry_failed_with_ytdl.py --all` |
