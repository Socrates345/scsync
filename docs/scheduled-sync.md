# Scheduled sync

The SoundCloud playlists sync by themselves every day at 18:00 through Windows Task Scheduler.

It runs on your own computer, not a VPS or CI, so your playlist links (your SoundCloud username and secret tokens) and your library never reach a third party. scdl runs without a login, from the same home IP you already use SoundCloud from.

## Set it up

From an **admin** PowerShell, in the repo folder, once (run it again to update the task):

```powershell
uv sync
powershell -ExecutionPolicy Bypass -File deploy\windows\register-task.ps1
```

This registers the task `scdl daily sync`. It runs `.venv\Scripts\python.exe src\sync_sc_playlists.py --unattended` from the repo folder:

- every day at 18:00, hidden, as you, even when you're logged out (no password is stored)
- if the computer was asleep or off at 18:00, as soon as it's back
- on battery too, for up to 3 hours

The script takes `-User`, `-At` and `-TaskName` to change those (`-At '07:30'`).

```powershell
Start-ScheduledTask -TaskName 'scdl daily sync'                       # run now
Get-ScheduledTaskInfo -TaskName 'scdl daily sync' | Select-Object LastRunTime, LastTaskResult, NextRunTime
Unregister-ScheduledTask -TaskName 'scdl daily sync' -Confirm:$false  # remove
```

`LastTaskResult`: `0` all good, `1` needs attention (see the alert or log), `75` another sync was running, `267009` still running.

## What an unattended run does differently

`--unattended` is the same sync as a manual run, except:

- **Output goes to a log file**: `logs/sc-sync_<date>_<time>.log`, newest 30 kept, starting with the python, scdl and ffmpeg paths it used. No progress bar.
- **No full sync of a playlist you already have.** If the quick check doesn't recognize a playlist (reshuffled, or the link now points elsewhere), it's reported as "needs a manual full sync" with the `--force-all --only <lines>` command, because a full sync can rename tracks `[unsync]` and you should see that happen. If the check itself errors, the playlist counts as failed and is tried again next run. Three of those in a row stop the run (offline or blocked). New playlists still get their first full download.
- **No YouTube retry.** Failed tracks wait for a manual run, or `python src/retry_failed_with_ytdl.py --all`.
- **The computer stays awake** while it runs.

Removed tracks are only ever detected by a `--force-all` you run yourself. Files you added to a playlist folder are never touched (a playlist rename moves the whole folder, those included).

## Alerts

The task can ping your phone through [ntfy](https://ntfy.sh). Copy `.env.example` to `.env` and set:

```ini
SC_NOTIFY_URL=https://ntfy.sh/<random topic>
```

The topic name works like a password, so make it random and use it for nothing else:

```powershell
python -c "import secrets; print(secrets.token_hex(16))"
```

Subscribe to that topic in the ntfy app, then check it works:

```powershell
python src/sync_sc_playlists.py --notify-test
```

An alert is sent when there are new tracks, a dead link, a playlist that needs a manual full sync, ffmpeg is missing, the run crashed, or the set of failing playlists changed since the last scheduled run (kept in `.sync-last.json`). A track that keeps failing alerts once, not every day. Nothing changed, no alert.

Alerts pass through ntfy's server, so they only hold counts, line numbers and fixed wording. Never usernames, links, playlist names, track names or error messages.

The setup script warns if `.env` has no `SC_NOTIFY_URL`. The sync still runs, just silently.
