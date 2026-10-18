"""scdl allows you to download music from Soundcloud

Usage:
    scdl (-l <track_url> | -s <search_query> | me) [-a | -f | -C | -t | -p | -r]
    [-c | --force-metadata][-o <offset>][--hidewarnings][--debug | --error]
    [--path <path>][--addtofile][--addtimestamp][--onlymp3][--hide-progress][--min-size <size>]
    [--max-size <size>][--no-album-tag][--no-playlist-folder]
    [--download-archive <file>][--sync][--extract-artist][--flac][--original-art]
    [--original-name][--original-metadata][--no-original][--only-original]
    [--name-format <format>][--strict-playlist][--playlist-name-format <format>]
    [--client-id <id>][--auth-token <token>][--overwrite][--no-playlist][--opus]
    [--add-description][--yt-dlp-args <argstring>][--failed-log <file>][--premium-log <file>]

    scdl -h | --help
    scdl --version


Options:
    -h --help                       Show this screen
    --version                       Show version
    -l [url]                        URL can be track/playlist/user
    -s [search_query]               Search for a track/playlist/user and use the first result
    -a                              Download all tracks of user (including reposts)
    -t                              Download all uploads of a user (no reposts)
    -f                              Download all favorites (likes) of a user
    -C                              Download all tracks commented on by a user
    -p                              Download all playlists of a user
    -r                              Download all reposts of user
    -c                              Continue if a downloaded file already exists
    --force-metadata                This will set metadata on already downloaded track
    -o [offset]                     Start downloading a playlist from the [offset]th track
                                    Indexing starts with 1.
    --addtimestamp                  Add track creation timestamp to filename,
                                    which allows for chronological sorting
                                    (Deprecated. Use --name-format instead.)
    --addtofile                     Add artist to filename if missing
    --debug                         Set log level to DEBUG
    --error                         Set log level to ERROR
    --download-archive [file]       Keep track of track IDs in an archive file,
                                    and skip already-downloaded files
    --extract-artist                Set artist tag from title instead of username
    --hide-progress                 Hide the wget progress bar
    --hidewarnings                  Hide Warnings. (use with precaution)
    --max-size [max-size]           Skip tracks larger than size (k/m/g)
    --min-size [min-size]           Skip tracks smaller than size (k/m/g)
    --no-playlist-folder            Download playlist tracks into main directory,
                                    instead of making a playlist subfolder
    --onlymp3                       Download only mp3 files
    --path [path]                   Use a custom path for downloaded files
    --sync                          Compares an auto-managed archive to a playlist and
                                    downloads/removes any changed tracks. Archive is stored in
                                    archive_trackers/<artist>_<slug>.txt relative to the current
                                    working directory
    --flac                          Convert original files to .flac. Only works if the original
                                    file is lossless quality
    --no-album-tag                  On some player track get the same cover art if from the same
                                    album, this prevent it
    --original-art                  Download original cover art, not just 500x500 JPEG
    --original-name                 Do not change name of original file downloads
    --original-metadata             Do not change metadata of original file downloads
    --no-original                   Do not download original file; only mp3, m4a, or opus
    --only-original                 Only download songs with original file available
    --name-format [format]          Specify the downloaded file name format. Use "-" to download
                                    to stdout
    --playlist-name-format [format] Specify the downloaded file name format, if it is being
                                    downloaded as part of a playlist
    --client-id [id]                Specify the client_id to use
    --auth-token [token]            Specify the auth token to use
    --overwrite                     Overwrite file if it already exists
    --strict-playlist               Abort playlist downloading if one track fails to download
    --no-playlist                   Skip downloading playlists
    --add-description               Adds the description to a separate txt file
    --opus                          Prefer downloading opus streams over mp3 streams
    --yt-dlp-args [argstring]       String with custom args to forward to yt-dlp
    --failed-log [file]             Append failed track info to this file (one line per failure)
    --premium-log [file]            Append GO+ preview tracks (≤30s, skipped by duration filter)
                                    to this file
"""

from __future__ import annotations

import configparser
import difflib
import importlib
import importlib.metadata
import logging
import posixpath
import re
import shlex
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlparse

import mutagen
from docopt import docopt
from soundcloud import (
    AlbumPlaylist,
    SoundCloud,
    Track,
    User,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import locked_file, sanitize_filename

from scdl import utils
from scdl.patches.archive_paths import to_relative
from scdl.patches.mutagen_postprocessor import MutagenPP
from scdl.patches.original_filename_preprocessor import OriginalFilenamePP
from scdl.patches.snip_detection_postprocessor import SnipDetectionPP
from scdl.patches.switch_outtmpl_preprocessor import OuttmplPP
from scdl.patches.sync_download_archive import SyncDownloadHelper

if TYPE_CHECKING:
    if sys.version_info < (3, 11):
        from typing_extensions import Unpack
    else:
        from typing import Unpack

logging.setLoggerClass(utils.YTLogger)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SCDLArgs(TypedDict):
    C: bool
    a: bool
    add_description: bool
    addtimestamp: bool
    addtofile: bool
    auth_token: str | None
    c: bool
    client_id: str | None
    debug: bool
    download_archive: str | None
    error: bool
    extract_artist: bool
    f: bool
    flac: bool
    force_metadata: bool
    hide_progress: bool
    hidewarnings: bool
    l: str  # noqa: E741
    max_size: str | None
    me: bool
    min_size: str | None
    name_format: str
    no_album_tag: bool
    no_original: bool
    no_playlist: bool
    no_playlist_folder: bool
    o: int | None
    only_original: bool
    onlymp3: bool
    opus: bool
    original_art: bool
    original_metadata: bool
    original_name: bool
    overwrite: bool
    p: bool
    path: Path
    playlist_name_format: str
    r: bool
    strict_playlist: bool
    sync: str | bool | None
    s: str | None
    t: bool
    yt_dlp_args: str
    failed_log: str | None
    premium_log: str | None


__version__ = importlib.metadata.version("scdl")


def _main() -> None:
    """Main function, parses the URL from command line arguments"""
    logger.addHandler(logging.StreamHandler())

    # Parse arguments
    arguments = docopt(__doc__, version=__version__)

    if arguments["--debug"]:
        logger.level = logging.DEBUG
    elif arguments["--error"]:
        logger.level = logging.ERROR

    config_file = Path(__file__).with_name("scdl.cfg")

    # import conf file
    config = _get_config(config_file)

    logger.info(f"[scdl] SCDL version {__version__}")

    client_id = arguments["--client-id"] or config["scdl"]["client_id"]
    token = arguments["--auth-token"] or config["scdl"]["auth_token"]

    client = SoundCloud(client_id, token if token else None)

    if not client.is_client_id_valid():
        if arguments["--client-id"]:
            logger.warning(
                "[scdl] Invalid client_id specified by --client-id argument. "
                "Using a dynamically generated client_id",
            )
        elif config["scdl"]["client_id"]:
            logger.warning(
                f"[scdl] Invalid client_id in {config_file}. Using a dynamically generated client_id",
            )
        else:
            logger.info("[scdl] Generating dynamic client_id")
        client = SoundCloud(None, token if token else None)
        if not client.is_client_id_valid():
            logger.error("[scdl] Dynamically generated client_id is not valid")
            sys.exit(1)
        config["scdl"]["client_id"] = client.client_id
        arguments["--client-id"] = client.client_id
        # save client_id
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(config_file, "w", encoding="utf-8") as f:
            config.write(f)

    if (token or arguments["me"]) and not client.is_auth_token_valid():
        if arguments["--auth-token"]:
            logger.error("[scdl] Invalid auth_token specified by --auth-token argument")
        else:
            logger.error(f"[scdl] Invalid auth_token in {config_file}")
        sys.exit(1)

    if arguments["-o"] is not None:
        try:
            arguments["-o"] = int(arguments["-o"])
            if arguments["-o"] < 1:
                raise ValueError
        except Exception:
            logger.error("[scdl] Offset should be a positive integer")
            sys.exit(1)

    if not arguments["--name-format"]:
        arguments["--name-format"] = config["scdl"]["name_format"]

    if not arguments["--playlist-name-format"]:
        arguments["--playlist-name-format"] = config["scdl"]["playlist_name_format"]

    if not arguments["--yt-dlp-args"]:
        arguments["--yt-dlp-args"] = config["scdl"].get("yt_dlp_args", "")

    if arguments["me"]:
        # set url to profile associated with auth token
        me = client.get_me()
        assert me is not None
        arguments["-l"] = me.permalink_url

    if arguments["-s"]:
        url = _search_soundcloud(client, arguments["-s"])
        if url:
            arguments["-l"] = url
        else:
            logger.error("[scdl] Search failed")
            sys.exit(1)

    arguments["--path"] = Path(arguments["--path"] or config["scdl"]["path"] or ".").resolve()

    # Derive a stable archive name from the URL — used for sync, failed, and premium logs
    url_path = urlparse(arguments["-l"]).path.strip("/")
    parts = [p for p in url_path.split("/") if p != "sets"]
    archive_name = "_".join(parts) if parts else "unknown"
    archive_dir = Path.cwd() / "archive_trackers" / "sc"
    archive_dir.mkdir(parents=True, exist_ok=True)

    if arguments["--sync"]:
        arguments["--sync"] = str(archive_dir / f"{archive_name}.txt")
        logger.info(f"[scdl] Sync archive: {arguments['--sync']}")

    if not arguments["--failed-log"]:
        arguments["--failed-log"] = str(archive_dir / f"{archive_name}.failed")

    if not arguments["--premium-log"]:
        arguments["--premium-log"] = str(archive_dir / f"{archive_name}.premium")

    # convert arguments dict to python-friendly kwarg names (no hyphens)
    python_args = {}
    for key, value in arguments.items():
        key = key.strip("-").replace("-", "_")
        python_args[key] = value

    python_args["client_id"] = client.client_id
    python_args["auth_token"] = client.auth_token
    url = python_args.pop("l")

    assert url is not None

    download_url(url, **python_args)


def _search_soundcloud(client: SoundCloud, query: str) -> str | None:
    """Search SoundCloud and return the URL of the first result."""
    try:
        results = list(client.search(query, limit=1))
        if results:
            item = results[0]
            logger.info(f"Search resolved to url {item.permalink_url}")
            if isinstance(item, (Track, AlbumPlaylist, User)):
                return item.permalink_url
            logger.warning(f"Unexpected search result type: {type(item)}")
        logger.error(f"No results found for query: {query}")
        return None
    except Exception as e:
        logger.error(f"Error searching SoundCloud: {e}")
        return None


def _get_config(config_file: Path) -> configparser.RawConfigParser:
    """Gets config from scdl.cfg"""
    config = configparser.RawConfigParser()

    default_config_file = Path(__file__).with_name("scdl.cfg")

    config_file.parent.mkdir(parents=True, exist_ok=True)

    # load default config first
    with open(default_config_file, encoding="utf-8") as f:
        config.read_file(f)

    try:
        with locked_file(config_file, "r", encoding="utf-8") as f:
            config.read_file(f)
    except Exception as err:
        logger.warning(f"Error while reading config file: {err}")

    try:
        with locked_file(config_file, "w", encoding="utf-8") as f:
            config.write(f)
    except Exception as err:
        logger.warning(f"Error while writing config file: {err}")

    return config


def _convert_v2_name_format(s: str) -> str:
    replacements = {
        "{id}": "%(id)s",
        "{user[username]}": "%(uploader)s",
        "{user[id]}": "%(uploader_id)s",
        "{user[permalink_url]}": "%(uploader_url)s",
        "{timestamp}": "%(timestamp)s",
        "{title}": "%(title)s",
        "{description}": "%(description)s",
        "{duration}": "%(duration)s",
        "{permalink_url}": "%(webpage_url)s",
        "{license}": "%(license)s",
        "{playback_count}": "%(view_count)s",
        "{likes_count}": "%(like_count)s",
        "{comment_count}": "%(comment_count)s",
        "{reposts_count}": "%(respost_count)s",
        "{playlist[author]}": "%(playlist_uploader)s",
        "{playlist[title]}": "%(playlist)s",
        "{playlist[id]}": "%(playlist_id)s",
        "{playlist[tracknumber]}": "%(playlist_index)s",
        "{playlist[tracknumber_total]}": "%(playlist_count)s",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    if not s.endswith(".%(ext)s"):
        s += ".%(ext)s"
    return s


def _build_ytdl_output_filename(scdl_args: SCDLArgs, in_playlist: bool, force_suffix: str | None = None) -> str:
    if scdl_args.get("name_format") == "-":
        return "-"

    playlist_format = "%(playlist|)s"
    if in_playlist:
        track_format = _convert_v2_name_format(scdl_args["playlist_name_format"])
    else:
        track_format = _convert_v2_name_format(scdl_args["name_format"])

    if scdl_args.get("addtimestamp") or scdl_args.get("addtofile"):
        track_format = "%(title)s.%(ext)s"
        if scdl_args.get("addtofile"):
            track_format = "%(uploader)s - " + track_format
        if scdl_args.get("addtimestamp"):
            track_format = "%(timestamp)s_" + track_format

    base = scdl_args["path"]
    if scdl_args.get("no_playlist_folder") or not in_playlist:
        ret = base / track_format
    else:
        ret = base / playlist_format / track_format

    if force_suffix:
        ret = ret.with_suffix(force_suffix)

    return ret.as_posix()


def _build_ytdl_format_specifier(scdl_args: SCDLArgs) -> str:
    fmt = "ba"
    if scdl_args.get("min_size"):
        fmt += f"[filesize_approx>={scdl_args['min_size']}]"
    if scdl_args.get("max_size"):
        fmt += f"[filesize_approx<={scdl_args['max_size']}]"
    if scdl_args.get("no_original"):
        fmt += "[format_id!=download]"
    if scdl_args.get("only_original"):
        fmt += "[format_id=download]"
    if scdl_args.get("onlymp3"):
        fmt += "[format_id*=mp3]"
    return fmt


def _build_ytdl_params(url: str, scdl_args: SCDLArgs) -> tuple[str, dict, list]:
    # return download url, ytdl params, and postprocessors

    if scdl_args.get("a"):
        pass
    elif scdl_args.get("t"):
        url = posixpath.join(url, "tracks")
    elif scdl_args.get("f"):
        url = posixpath.join(url, "likes")
    elif scdl_args.get("C"):
        url = posixpath.join(url, "comments")
    elif scdl_args.get("p"):
        url = posixpath.join(url, "sets")
    elif scdl_args.get("r"):
        url = posixpath.join(url, "reposts")

    params: dict = {}

    # default params
    params["--embed-metadata"] = True
    params["--embed-thumbnail"] = True
    params["--remux-video"] = "aac>m4a"
    params["--extractor-args"] = "soundcloud:formats=*_aac,*_mp3"  # ignore opus by default
    params["--use-extractors"] = "soundcloud.*"
    params["--output-na-placeholder"] = ""
    params["--parse-metadata"] = []
    params["--trim-filenames"] = "240b"
    postprocessors = [
        (
            OuttmplPP(
                _build_ytdl_output_filename(scdl_args, False),
                _build_ytdl_output_filename(scdl_args, True),
            ),
            "pre_process",
        )
    ]

    if scdl_args.get("strict_playlist"):
        params["--abort-on-error"] = True

    if not scdl_args.get("c") and not scdl_args.get("download_archive") and not scdl_args.get("sync"):
        params["--break-on-existing"] = True

    # if not scdl_args.get("force_metadata"):
    #     https://github.com/yt-dlp/yt-dlp/issues/1467
    #     params["--no-post-overwrites"] = True  # noqa: ERA001

    if scdl_args.get("o"):
        params["--playlist-items"] = f"{scdl_args.get('o')}:"

    if scdl_args.get("extract_artist"):
        params["--parse-metadata"] += [
            r"%(title)s:(?P<meta_artist>.*?)\s+[-−–—―]\s*(?P<meta_title>.*)",  # noqa: RUF001
        ]

    if scdl_args.get("debug"):
        params["--verbose"] = True

    if scdl_args.get("error"):
        params["--quiet"] = True

    if scdl_args.get("download_archive"):
        params["--download-archive"] = scdl_args.get("download_archive")

    if scdl_args.get("hide_progress"):
        params["--no-progress"] = True

    if scdl_args.get("max_size"):
        params["--max-filesize"] = scdl_args.get("max_size")
    if scdl_args.get("min_size"):
        params["--min-filesize"] = scdl_args.get("min_size")

    params["-f"] = _build_ytdl_format_specifier(scdl_args)

    if scdl_args.get("flac"):
        params["--recode-video"] = "aiff>flac/alac>flac/wav>flac"

    if not scdl_args.get("no_album_tag"):
        params["--parse-metadata"] += [
            "%(playlist)s:%(meta_album)s",
            "%(playlist_uploader)s:%(meta_album_artist)s",
            "%(playlist_index)s:%(meta_track)s",
        ]

    if scdl_args.get("original_name") and not scdl_args.get("no_original"):
        postprocessors.append((OriginalFilenamePP(), "pre_process"))

    if not scdl_args.get("original_art"):
        params["--thumbnail-id"] = "t500x500"

    if scdl_args.get("name_format") == "-":
        # https://github.com/yt-dlp/yt-dlp/issues/8815
        # https://github.com/yt-dlp/yt-dlp/issues/126
        params["--embed-metadata"] = False
        params["--embed-thumbnail"] = False

    if scdl_args.get("original_metadata"):
        params["--embed-metadata"] = False
        params["--embed-thumbnail"] = False
    else:
        postprocessors.append((MutagenPP(scdl_args["force_metadata"]), "post_process"))

    if scdl_args.get("auth_token"):
        params["--username"] = "oauth"
        params["--password"] = scdl_args.get("auth_token")

    if scdl_args.get("overwrite"):
        params["--force-overwrites"] = True

    if scdl_args.get("no_playlist"):
        params["--match-filters"] = "!playlist_uploader"

    if scdl_args.get("add_description"):
        params["--print-to-file"] = (
            "description",
            _build_ytdl_output_filename(scdl_args, False, ".txt"),
        )

    if scdl_args.get("opus"):
        params["--extractor-args"] = "soundcloud:formats=*_aac,*_opus,*_mp3"

    argv = []
    for param, value in params.items():
        if value is False:
            continue
        if value is True:
            argv.append(param)
        elif isinstance(value, list):
            for v in value:
                argv.append(param)
                argv.append(v)
        elif isinstance(value, tuple):
            argv.append(param)
            argv += list(value)
        else:
            argv.append(param)
            argv.append(value)

    _redacted = list(argv)
    for _flag in ("--password", "--username"):
        if _flag in _redacted:
            _i = _redacted.index(_flag)
            if _i + 1 < len(_redacted):
                _redacted[_i + 1] = "***"
    logger.debug(f"[debug] yt-dlp args: {url} {' '.join(_redacted)}")

    return url, utils.cli_to_api(argv), postprocessors


# Tags a genuinely different version/edit of a track. If exactly one side of a
# comparison has one of these (after normalization), the tracks must never be
# treated as duplicates, regardless of how similar the rest of the text is —
# token-set scoring treats subset matches (e.g. "Song" vs "Song Remix") as
# near-perfect, so this guards the one case that scorer can't tell apart.
_VERSION_MARKERS = frozenset({
    "remix", "acoustic", "live", "sped", "slowed", "nightcore", "cover",
    "extended", "instrumental", "edit", "vip", "bootleg", "flip", "mashup",
    "reprise",
})

# Purely decorative bracket/parenthetical content — safe to discard because it
# never changes which song is being referred to (unlike _VERSION_MARKERS).
_DECORATIVE_BRACKET_RE = re.compile(
    r"[\(\[\{]\s*(?:"
    r"official\s*(?:video|audio|music\s*video|visualizer|lyric\s*video)?"
    r"|lyrics?"
    r"|hq|hd|high\s*quality"
    r"|free\s*download|free\s*dl"
    r"|prod\.?\s*by\s+[^)\]}]+"
    r")\s*[\)\]\}]",
    re.IGNORECASE,
)
_FEAT_RE = re.compile(r"\b(feat\.?|ft\.?|featuring)\b", re.IGNORECASE)


def _normalize_track_name(name: str) -> str:
    """Strip filename decorations (playlist index, archive-id prefix, [unsync] marker,
    diacritics, decorative tags, feat./ft. variance, punctuation noise) so two names
    can be compared on title/uploader content alone. Used only for comparison — never
    affects what gets written to the log or to disk."""
    name = re.sub(r"^\d+\.\s*", "", name)
    name = re.sub(r"^\[(unsync|[^\]]*)\]\s*", "", name)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = _FEAT_RE.sub("feat", name)
    name = _DECORATIVE_BRACKET_RE.sub("", name)
    name = re.sub(r"[-–—~]+", " ", name)  # hyphen, en dash, em dash, tilde  # noqa: RUF001
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _token_set_ratio(a: str, b: str) -> float:
    """fuzzywuzzy-style token-set similarity (0-100), built on stdlib difflib only:
    compares the shared-token core against each side's full token set, so noise that
    appears on only one side (extra uploader text, a decorative suffix) doesn't tank
    the score the way a plain SequenceMatcher.ratio() over the full strings would."""
    tokens_a, tokens_b = set(a.split()), set(b.split())
    intersection = tokens_a & tokens_b
    t0 = " ".join(sorted(intersection))
    t1 = " ".join(sorted(intersection | tokens_a))
    t2 = " ".join(sorted(intersection | tokens_b))
    return max(
        difflib.SequenceMatcher(None, t0, t1).ratio(),
        difflib.SequenceMatcher(None, t0, t2).ratio(),
        difflib.SequenceMatcher(None, t1, t2).ratio(),
    ) * 100


def _get_audio_duration(path: Path) -> float | None:
    """Best-effort duration (seconds) of an existing audio file, for logging
    alongside match decisions. Never raises — a corrupt/partial file just yields None."""
    try:
        audio = mutagen.File(path)
        if audio is not None and audio.info is not None:
            return audio.info.length
    except Exception:
        pass
    return None


def _find_similar_existing_file(
    name: str, dest_dir: Path, title: str | None = None, duration: float | None = None, threshold: float = 90
) -> Path | None:
    """Look for a file already in dest_dir whose name closely matches `name` — e.g. a
    track downloaded earlier under a different ID, or placed there manually.

    `title` (uploader stripped out), if given, is also scored on its own against each
    candidate's full name and the better of the two scores is used. This is what
    catches reposts: when the uploader genuinely differs on both sides (not just one
    side having extra noise), token_set_ratio on the combined "uploader - title"
    string is diluted by two *different* uploader tokens — comparing title-only
    sidesteps that, since the existing file's uploader then just becomes one-sided
    noise (token_set_ratio's strength) instead of a second, conflicting signal.

    `duration` (seconds, if known) is only used for logging/diagnostics right now —
    see the plan doc for why it isn't gating the decision yet (legitimate duration
    variance across mixes/re-encodes means a hard cutoff would cause false negatives;
    proper combination needs empirical tuning against real data first)."""
    if not dest_dir.is_dir():
        return None
    candidate = _normalize_track_name(name)
    candidate_title = _normalize_track_name(title) if title else None
    candidate_tokens = set(candidate.split())
    best_match: Path | None = None
    best_score = 0.0
    for path in dest_dir.iterdir():
        if not path.is_file():
            continue
        existing = _normalize_track_name(path.stem)
        existing_tokens = set(existing.split())

        marker_asymmetry = (candidate_tokens & _VERSION_MARKERS) != (existing_tokens & _VERSION_MARKERS)
        if marker_asymmetry:
            continue

        score = _token_set_ratio(candidate, existing)
        if candidate_title:
            score = max(score, _token_set_ratio(candidate_title, existing))
        if logger.isEnabledFor(logging.DEBUG):
            existing_duration = _get_audio_duration(path)
            duration_diff = (
                abs(duration - existing_duration) if duration is not None and existing_duration is not None else None
            )
            logger.debug(f"[scdl] dedup candidate: {path.name!r} score={score:.1f} duration_diff={duration_diff}")

        if score > best_score:
            best_score, best_match = score, path
    return best_match if best_score >= threshold else None


def download_url(url: str, **scdl_args: Unpack[SCDLArgs]) -> None:
    url, params, postprocessors = _build_ytdl_params(url, scdl_args)

    params["logger"] = logger

    # we handle this with custom MutagenPP for now
    params["postprocessors"] = [
        pp for pp in params["postprocessors"] if pp["key"] not in ("EmbedThumbnail", "FFmpegMetadata")
    ]

    yt_dlp_args = scdl_args.get("yt_dlp_args")
    if yt_dlp_args:
        argv = shlex.split(yt_dlp_args)
        overrides = utils.cli_to_api(argv)
        params = {**params, **overrides}

    with YoutubeDL(params) as ydl:
        if scdl_args["client_id"]:
            ydl.cache.store("soundcloud", "client_id", scdl_args["client_id"])
        for pp, when in postprocessors:
            ydl.add_post_processor(pp, when)

        if scdl_args.get("failed_log"):
            from functools import partial as _partial

            failed_log_path = scdl_args["failed_log"]
            _fl_attempted: dict[str, dict] = {}  # archive_id -> {url, title, uploader}
            _fl_downloaded: set[str] = set()
            _fl_errors: dict[str, str] = {}      # archive_id -> first error message
            _current_aid: list[str] = [""]       # mutable ref updated per track
            _fl_playlist_title: list[str | None] = [None]

            _old_match_entry = ydl._match_entry

            def _fl_match_entry(ydl_, info_dict, incomplete=False, silent=False):
                result = _old_match_entry(info_dict, incomplete, silent)
                if _fl_playlist_title[0] is None and info_dict.get("playlist"):
                    _fl_playlist_title[0] = info_dict["playlist"]
                if result is None:
                    aid = ydl_._make_archive_id(info_dict)
                    if aid is None:  # skip playlist-level entries with no valid id
                        return result
                    _fl_attempted[aid] = {
                        "url": (
                            info_dict.get("webpage_url")
                            or info_dict.get("permalink_url")
                            or info_dict.get("original_url")
                            or info_dict.get("url")
                            or ""
                        ),
                        "title": info_dict.get("title") or "",
                        "uploader": info_dict.get("uploader") or "",
                        "duration": info_dict.get("duration"),
                    }
                    _current_aid[0] = aid
                return result

            ydl._match_entry = _partial(_fl_match_entry, ydl)

            _base_logger = ydl.params["logger"]

            # yt-dlp routes all screen output through logger.debug; the line
            # "[soundcloud] uploader/slug: Downloading info JSON" arrives here
            # before the format fetch, so we can backfill title/uploader.
            _sc_info_prefix = "[soundcloud] "
            _sc_info_suffix = ": Downloading info JSON"

            class _TrackErrorLogger:
                def debug(self, msg, *args, **kwargs):
                    _base_logger.debug(msg, *args, **kwargs)
                    if (
                        isinstance(msg, str)
                        and _current_aid[0]
                        and _current_aid[0] in _fl_attempted
                        and msg.startswith(_sc_info_prefix)
                        and msg.endswith(_sc_info_suffix)
                    ):
                        slug = msg[len(_sc_info_prefix):-len(_sc_info_suffix)]
                        parts = slug.split("/")
                        entry = _fl_attempted[_current_aid[0]]
                        if not entry["uploader"] and len(parts) >= 1:
                            entry["uploader"] = parts[0]
                        if not entry["title"] and len(parts) >= 2:
                            entry["title"] = parts[1].replace("-", " ")

                def info(self, msg, *args, **kwargs): _base_logger.info(msg, *args, **kwargs)
                def warning(self, msg, *args, **kwargs): _base_logger.warning(msg, *args, **kwargs)

                def error(self, msg, *args, **kwargs):
                    if _current_aid[0]:
                        _fl_errors.setdefault(_current_aid[0], str(msg))
                    _base_logger.error(msg, *args, **kwargs)

            ydl.params["logger"] = _TrackErrorLogger()

            def _fl_track_done(d):
                if d["status"] == "finished":
                    _fl_downloaded.add(ydl._make_archive_id(d["info_dict"]))

            ydl.add_progress_hook(_fl_track_done)

        if scdl_args.get("premium_log"):
            from functools import partial as _partial

            premium_log_path = scdl_args["premium_log"]
            _prem_old_match_entry = ydl._match_entry

            def _prem_match_entry(_ydl, info_dict, incomplete=False, silent=False):
                result = _prem_old_match_entry(info_dict, incomplete, silent)
                if result is not None and not incomplete:
                    duration = info_dict.get("duration")
                    if duration is not None and duration <= 30:
                        track_url = (
                            info_dict.get("webpage_url")
                            or info_dict.get("original_url")
                            or info_dict.get("url")
                            or ""
                        )
                        title = info_dict.get("title", "unknown")
                        if premium_log_path is not None:
                            with open(premium_log_path, "a", encoding="utf-8") as fh:
                                fh.write(f"{title} | {track_url} | {duration:.1f}s\n")
                return result

            ydl._match_entry = _partial(_prem_match_entry, ydl)

        sync = SyncDownloadHelper(scdl_args, ydl)

        def _on_snip_detected(info: dict, actual_duration: float) -> None:
            archive_id = ydl._make_archive_id(info)
            if archive_id is None:
                return
            sync.discard(archive_id)
            if scdl_args.get("failed_log"):
                _fl_downloaded.discard(archive_id)
                entry = _fl_attempted.get(archive_id)
                if entry is None:
                    entry = _fl_attempted[archive_id] = {
                        "url": (
                            info.get("webpage_url")
                            or info.get("permalink_url")
                            or info.get("original_url")
                            or info.get("url")
                            or ""
                        ),
                        "title": info.get("title") or "",
                        "uploader": info.get("uploader") or "",
                        "duration": info.get("duration"),
                    }
                track_id = archive_id.split()[-1]
                reported = info.get("duration")
                _fl_errors[archive_id] = (
                    f"[soundcloud] {track_id}: GO+ preview snip — actual audio is {actual_duration:.1f}s"
                    + (f", not the reported {reported:.0f}s" if reported else "")
                )

        ydl.add_post_processor(SnipDetectionPP(on_snip=_on_snip_detected), "after_move")

        ydl.download(url)
        sync.post_download()

        if scdl_args.get("failed_log"):
            import json

            def _sc_track_policy(track_id: str) -> str:
                """Return the SoundCloud policy field for a track, or '' on failure.
                Uses ydl's HTTP stack so proxy/DNS config matches the main download."""
                client_id = scdl_args.get("client_id", "")
                api_url = f"https://api-v2.soundcloud.com/tracks/{track_id}?client_id={client_id}"
                try:
                    with ydl.urlopen(api_url) as resp:
                        return json.loads(resp.read()).get("policy", "")
                except Exception:
                    return ""

            in_playlist_folder = bool(_fl_playlist_title[0]) and not scdl_args.get("no_playlist_folder")
            dest_dir = (
                scdl_args["path"] / sanitize_filename(_fl_playlist_title[0], restricted=False)
                if in_playlist_folder
                else scdl_args["path"]
            )
            _fl_found: dict[str, Path] = {}  # archive_id -> matched existing file path

            assert failed_log_path is not None
            with open(failed_log_path, "w", encoding="utf-8") as fh:
                for archive_id, info in _fl_attempted.items():
                    if archive_id not in _fl_downloaded:
                        track_url = info["url"]
                        name = " - ".join(filter(None, [info["uploader"], info["title"]])) or archive_id
                        error = _fl_errors.get(archive_id, "")

                        found_match = _find_similar_existing_file(
                            name, dest_dir, title=info.get("title"), duration=info.get("duration")
                        )

                        if found_match is not None:
                            # Already resolved — gets added to the sync archive below,
                            # so it shouldn't also linger in .failed as if unresolved.
                            _fl_found[archive_id] = found_match
                            continue
                        if "HTTP Error 404" in error:
                            track_id = archive_id.split()[-1]
                            policy = _sc_track_policy(track_id)
                            if policy == "SNIP":
                                tag = "[GO+]     "
                                error_line = ""
                            elif policy == "BLOCK":
                                tag = "[BLOCKED] "
                                error_line = ""
                            elif policy == "MONETIZE":
                                tag = "[MONETIZE]"
                                error_line = ""
                            else:
                                tag = "[FAIL]    "
                                error_line = (
                                    f"       → policy={policy!r} — {error.strip()[:160]}\n"
                                    if policy
                                    else f"       → {error.strip()[:200]}\n"
                                )
                        else:
                            tag = "[FAIL]    "
                            error_line = f"       → {error.strip()[:200]}\n" if error else ""

                        fh.write(f"{tag} {name}\n")
                        fh.write(f"         {track_url}\n")
                        if error_line:
                            fh.write(error_line)
                        fh.write("\n")

            # A [FOUND] track already exists on disk — record it in the sync archive so
            # future syncs treat it as downloaded instead of re-attempting and re-matching it.
            if _fl_found and scdl_args.get("sync"):
                with locked_file(scdl_args["sync"], "a", encoding="utf-8") as archive_file:
                    for archive_id, matched_path in _fl_found.items():
                        archive_file.write(f"{archive_id} {to_relative(matched_path)}\n")


if __name__ == "__main__":
    _main()
