# Development

## Setup

```powershell
uv sync --dev
. .\.venv\Scripts\Activate.ps1
```

```powershell
ruff check scdl/
ruff format scdl/
mypy
pytest
```

## Keeping up to date

Two separate updates.

**yt-dlp**, whenever `ytdl.py` or an alert says it's old:

```powershell
uv sync --upgrade-package yt-dlp
```

**Upstream scdl**, monthly or when SoundCloud changes something:

```powershell
git fetch upstream
git merge upstream/master
uv sync          # if pyproject.toml changed
ruff check scdl/
mypy
pytest
```

The merge is almost always clean. The only file that conflicts is `scdl/scdl.py`: `src/`, `scdl/patches/`, `deploy/` and the configs are local and upstream never touches them. If `pyproject.toml` conflicts, it's usually the `yt-dlp` version: take upstream's, and add back `tqdm>=4.0.0` (used by `src/sync_sc_playlists.py`).

Don't `pipx install scdl` for this repo: that installs upstream scdl, without the sync changes. The `scdl` command comes from the venv.

## What's added on top of scdl

| Path | What it is |
| --- | --- |
| `src/sync_sc_playlists.py` | Syncs every playlist in `src/sc-playlists-list.md` ([how](how-sync-works.md)) |
| `src/retry_failed_with_ytdl.py` | Gets `[FAIL]` tracks from YouTube |
| `src/cleanup_short_tracks.py` | Finds GO+ 30-second snips already on disk |
| `src/cleanup_empty_archives.py` | Deletes empty files in `archive_trackers/sc` (run at the end of every sync) |
| `src/ytdl.py`, `src/sync_yt_playlists.py` | YouTube audio and video sync, reusing scdl's archive handling |
| `src/ytdlp_age.py` | Says when the installed yt-dlp is worth upgrading |
| `src/dl_videos_bulk_from_txt.bat` | Downloads every video in `src/videos_bulk.txt` |
| `deploy/windows/register-task.ps1` | Registers the daily task ([scheduled-sync.md](scheduled-sync.md)) |
| `scdl/patches/sync_download_archive.py` | The `--sync` archive: `[unsync]`, dead-link guard, playlist renames |
| `scdl/patches/archive_paths.py` | Archive paths relative to the repo folder |
| `scdl/patches/snip_detection_postprocessor.py` | Throws away GO+ previews that got past the `duration>30` filter |

Other patches: `mutagen_postprocessor.py` (tags and cover art), `switch_outtmpl_preprocessor.py` (track vs playlist file names), `original_filename_preprocessor.py`, `thumbnail_selection.py` (500x500 art), `trim_filenames.py` (240-byte limit) and `old_archive_ids.py` (pre-v3 archives).

`scdl/scdl.py` is the entry point: `_main()` parses the CLI (docopt), loads `scdl.cfg` and calls `download_url()`, which builds the `YoutubeDL` instance and adds the patches.

### One-time migrations

Kept for old libraries. Both are safe to run twice.

```powershell
python src/migrate_relative_archive_paths.py           # dry run: absolute archive paths -> relative
python src/migrate_relative_archive_paths.py --write   # apply
python src/migrate_remove_indices.py --dry-run         # preview: strip the "001. " prefix from playlist file names
python src/migrate_remove_indices.py                   # apply (writes by default)
```

## Privacy

`scdl/scdl.cfg` is tracked (upstream ships it). Never put an `auth_token` in it. The playlist lists, `ytdl.cfg`, `.env`, `archive_trackers/`, `logs/` and the sync state files are git-ignored: archive names and logs can contain your username and secret tokens. Use placeholders like `<user>/sets/<playlist>/s-<token>` in code, docs and examples.

## TODO

- Every deletion of a song must be approved by a human. Many songs were added to playlist folders by hand.
- Keep a list, per playlist, of the songs in its folder that aren't in the SoundCloud playlist.
