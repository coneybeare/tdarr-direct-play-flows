#!/usr/bin/env python3
"""Query Tdarr instances and analyze file state for flow issues.

Usage:
    python3 scripts/analyze_tdarr.py HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --servers              # reads servers.local.json
    python3 scripts/analyze_tdarr.py --api-key SECRET HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --requeue HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --delete-errors HOST [HOST ...]

Examples:
    python3 scripts/analyze_tdarr.py http://localhost:8265
    python3 scripts/analyze_tdarr.py --servers
    python3 scripts/analyze_tdarr.py --servers --status errors
    python3 scripts/analyze_tdarr.py --servers --search "ozark"
    python3 scripts/analyze_tdarr.py --servers --requeue-file "liya silver"
    python3 scripts/analyze_tdarr.py --queued http://localhost:8265
    python3 scripts/analyze_tdarr.py --status all http://localhost:8265
    python3 scripts/analyze_tdarr.py --requeue http://localhost:8265
    python3 scripts/analyze_tdarr.py --delete-errors http://localhost:8265

Tdarr API notes:
    - POST /api/v2/cruddb is the main endpoint for all DB operations.
    - FileJSONDB getAll returns a dict keyed by file path, NOT a list.
      docFilter is broken server-side; always returns all files regardless
      of filter — filter client-side instead.
    - To update a file record, use mode "update" with the "obj" field
      (NOT "update"). Using "update" instead of "obj" silently ignores
      the change. Reference: https://github.com/HaveAGitGat/Tdarr/issues/752
    - File _id equals the file path (the dict key from getAll).
    - GET /api/v2/status returns server version, uptime, and OS info.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


# ── Tdarr API helpers ─────────────────────────────────────────────────────────


def _post(url: str, payload: dict, api_key: str | None = None) -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _get(url: str, api_key: str | None = None) -> dict:
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_status(host: str, api_key: str | None = None) -> dict:
    return _get(f"{host}/api/v2/status", api_key)


def get_all_files(host: str, api_key: str | None = None) -> list[dict]:
    """Fetch all files from the FileJSONDB collection.

    Tdarr returns a dict keyed by file path (not a list).  The docFilter
    parameter is broken server-side and always returns everything, so we
    fetch all and let callers filter client-side.
    """
    payload = {
        "data": {
            "collection": "FileJSONDB",
            "mode": "getAll",
            "docFilter": {},
        }
    }
    resp = _post(f"{host}/api/v2/cruddb", payload, api_key)
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        # Tdarr returns {file_path: file_record, ...} — extract values
        first = next(iter(resp.values()), None) if resp else None
        if first and isinstance(first, dict) and ("file" in first or "_id" in first):
            return list(resp.values())
        # Fallback: check for nested list keys
        for key in ("docs", "data", "files", "results"):
            if key in resp and isinstance(resp[key], list):
                return resp[key]
        if "_id" in resp:
            return [resp]
    return []


def requeue_file(
    host: str, file_id: str, api_key: str | None = None
) -> bool:
    """Set a file's TranscodeDecisionMaker to 'Queued'.

    Uses the cruddb update mode with the ``obj`` field (not ``update``).
    The ``update`` field silently ignores changes — ``obj`` is required.
    Reference: https://github.com/HaveAGitGat/Tdarr/issues/752

    The update endpoint may return an empty body on success, so we check
    HTTP status rather than parsing JSON.
    """
    payload = {
        "data": {
            "collection": "FileJSONDB",
            "mode": "update",
            "docID": file_id,
            "obj": {"TranscodeDecisionMaker": "Queued"},
        }
    }
    try:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        req = urllib.request.Request(
            f"{host}/api/v2/cruddb", data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, Exception):
        return False


def delete_file(
    host: str, file_id: str, api_key: str | None = None
) -> tuple[bool, str]:
    """Delete a file from disk and remove it from the Tdarr database.

    Returns (success, message) so callers can report what went wrong.
    The /api/v2/delete-file endpoint may return an empty body on success.
    """
    payload = {"data": {"file": {"_id": file_id}}}
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        f"{host}/api/v2/delete-file", data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True, "OK"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Connection error: {e.reason}"
    except Exception as e:
        return False, str(e)


def _tdarr_path_to_local(tdarr_path: str, mount_prefix: str = "/Volumes") -> str:
    """Convert a Tdarr file path to a local SMB mount path.

    Tdarr paths like /movies/Foo/bar.mp4 map to /Volumes/Movies/Foo/bar.mp4.
    The first path component is title-cased to match macOS mount names.
    """
    parts = PurePosixPath(tdarr_path).parts
    if len(parts) < 2:
        return tdarr_path
    # /movies → Movies, /television → Television, /other → Other
    share_name = parts[1].title()
    rest = str(PurePosixPath(*parts[2:])) if len(parts) > 2 else ""
    return str(Path(mount_prefix) / share_name / rest)


def probe_file(local_path: str) -> dict | None:
    """Run ffprobe on a local file and return parsed JSON, or None on error."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_streams",
                "-show_format",
                "-print_format", "json",
                local_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def _safe_float(value, default: float = 0.0) -> float:
    """Safely parse a numeric value, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _print_probe_result(probe_data: dict) -> None:
    """Print a concise summary of ffprobe results."""
    streams = probe_data.get("streams", [])
    fmt = probe_data.get("format", {})
    dur_s = _safe_float(fmt.get("duration"))
    br = _safe_float(fmt.get("bit_rate")) / 1000
    encoder = fmt.get("tags", {}).get("encoder", "")

    video = [s for s in streams if s.get("codec_type") == "video"
             and s.get("codec_name") not in ("mjpeg", "png", "gif")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    print(f"      Disk probe ({len(streams)} streams, {dur_s / 60:.0f} min, {br:.0f} kbps):")
    for v in video:
        tag = v.get("codec_tag_string", "?")
        enc = (v.get("tags") or {}).get("encoder", "")
        w = v.get("width", "?")
        h = v.get("height", "?")
        print(f"        video/{v.get('codec_name','?')} ({tag}) {w}x{h}"
              + (f" enc={enc}" if enc else ""))
    for a in audio:
        lang = (a.get("tags") or {}).get("language", "?")
        print(f"        audio/{a.get('codec_name','?')} {a.get('channels','?')}ch lang={lang}")
    if not video and not audio:
        print("        (no video/audio streams)")
    if encoder:
        print(f"        muxer: {encoder}")
    # Diagnosis
    if not audio:
        print("        >> CORRUPT: no audio on disk")
    if not video:
        print("        >> CORRUPT: no video on disk")
    if not streams:
        print("        >> CORRUPT: no streams at all")


# ── Arr integration ──────────────────────────────────────────────────────────


def _parse_series_episode(tdarr_path: str) -> tuple[str, int, int] | None:
    """Extract series name, season, and episode from a Tdarr TV path.

    Expects paths like /television/Show Name/Season N/Show Name - SXXEXX - Title.mp4
    Returns (series_name, season, episode) or None if not parseable.
    """
    import re

    name = PurePosixPath(tdarr_path).stem
    m = re.search(r"S(\d+)E(\d+)", name)
    if not m:
        return None
    season = int(m.group(1))
    episode = int(m.group(2))
    # Series name is everything before " - SXXEXX"
    series_name = name[: m.start()].rstrip(" -")
    return series_name, season, episode


def _parse_movie(tdarr_path: str) -> tuple[str, int | None] | None:
    """Extract movie title and year from a Tdarr movie path.

    Expects paths like /movies/Title (YEAR)/Title (YEAR) Quality.mp4
    Returns (title, year) or None if not parseable.
    """
    import re

    parts = PurePosixPath(tdarr_path).parts
    if len(parts) < 3:
        return None
    folder = parts[2]  # e.g. "Title (2020)"
    m = re.search(r"^(.+?)\s*\((\d{4})\)", folder)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return folder, None


def arr_search_movie(
    arr_host: str, arr_key: str, title: str, year: int | None = None
) -> str:
    """Find a movie in Radarr and trigger a search for replacement.

    Returns a status message.
    """
    headers = {"Content-Type": "application/json", "X-Api-Key": arr_key}
    search_term = urllib.parse.quote(title, safe="")
    req = urllib.request.Request(
        f"{arr_host}/api/v3/movie/lookup?term={search_term}", headers=headers
    )
    results = json.loads(urllib.request.urlopen(req, timeout=30).read())

    found = None
    for r in results:
        if r.get("id"):
            if year and r.get("year") == year:
                found = r
                break
            elif not found:
                found = r
    if not found:
        return "not found in Radarr"

    mid = found["id"]
    # Enable monitoring (best-effort — PUT can fail on stale movie data)
    try:
        req2 = urllib.request.Request(
            f"{arr_host}/api/v3/movie/{mid}", headers=headers
        )
        movie = json.loads(urllib.request.urlopen(req2, timeout=30).read())
        movie["monitored"] = True
        req3 = urllib.request.Request(
            f"{arr_host}/api/v3/movie/{mid}",
            data=json.dumps(movie).encode(),
            headers=headers,
            method="PUT",
        )
        urllib.request.urlopen(req3, timeout=30)
        monitored = True
    except Exception:
        monitored = False

    # Refresh then search
    for cmd_name in ["RefreshMovie", "MoviesSearch"]:
        cmd = {"name": cmd_name, "movieIds": [mid]}
        req4 = urllib.request.Request(
            f"{arr_host}/api/v3/command",
            data=json.dumps(cmd).encode(),
            headers=headers,
        )
        urllib.request.urlopen(req4, timeout=30)

    status = "monitored + " if monitored else ""
    return f"{status}search queued (id={mid})"


def arr_search_episode(
    arr_host: str,
    arr_key: str,
    series_name: str,
    season: int,
    episode: int,
) -> str:
    """Find a series/episode in Sonarr and trigger a search for replacement.

    Returns a status message.
    """
    headers = {"Content-Type": "application/json", "X-Api-Key": arr_key}
    search_term = urllib.parse.quote(series_name, safe="")
    req = urllib.request.Request(
        f"{arr_host}/api/v3/series/lookup?term={search_term}", headers=headers
    )
    results = json.loads(urllib.request.urlopen(req, timeout=30).read())

    found = None
    for r in results:
        if r.get("id"):
            found = r
            break
    if not found:
        return "series not found in Sonarr"

    sid = found["id"]
    # Get episodes
    req2 = urllib.request.Request(
        f"{arr_host}/api/v3/episode?seriesId={sid}", headers=headers
    )
    eps = json.loads(urllib.request.urlopen(req2, timeout=30).read())

    ep_id = None
    monitored = False
    for ep in eps:
        if ep.get("seasonNumber") == season and ep.get("episodeNumber") == episode:
            ep_id = ep["id"]
            # Enable monitoring (best-effort)
            try:
                ep["monitored"] = True
                req3 = urllib.request.Request(
                    f"{arr_host}/api/v3/episode/{ep_id}",
                    data=json.dumps(ep).encode(),
                    headers=headers,
                    method="PUT",
                )
                urllib.request.urlopen(req3, timeout=30)
                monitored = True
            except Exception:
                pass
            break

    if not ep_id:
        return f"S{season:02d}E{episode:02d} not found in Sonarr"

    cmd = {"name": "EpisodeSearch", "episodeIds": [ep_id]}
    req4 = urllib.request.Request(
        f"{arr_host}/api/v3/command",
        data=json.dumps(cmd).encode(),
        headers=headers,
    )
    urllib.request.urlopen(req4, timeout=30)
    status = "monitored + " if monitored else ""
    return f"{status}search queued (ep_id={ep_id})"


def _get_arr_config() -> dict[str, dict]:
    """Load arr config from servers.local.json keyed by Tdarr host.

    Returns {host: {"arr": "radarr"|"sonarr", "arr_host": ..., "arr_api_key": ...}}
    """
    servers_file = Path(__file__).resolve().parent.parent / "servers.local.json"
    if not servers_file.exists():
        return {}
    with open(servers_file) as fh:
        data = json.load(fh)
    result = {}
    for srv in data.get("servers", []):
        host = srv["host"].rstrip("/")
        arr = srv.get("overrides", {}).get("arr_notify", {})
        if arr:
            result[host] = arr
    return result


# ── Analysis ──────────────────────────────────────────────────────────────────

UNWANTED_AUDIO_CODECS = {
    "ac3",
    "mp3",
    "dts",
    "truehd",
    "mlp",
    "flac",
    "vorbis",
    "opus",
    "wma",
    "wmav2",
    "wmapro",
    "dca",
    "pcm_s16le",
    "pcm_s24le",
}

# Statuses that mean the file has been through the flow
PROCESSED_STATUSES = {"Transcode success", "Not required"}


@dataclass
class FileIssue:
    severity: str  # "error", "warning", "info"
    message: str


@dataclass
class FileReport:
    path: str
    name: str
    container: str
    video_codec: str
    video_tag: str
    resolution: str
    audio_streams: list[dict]
    file_size_mb: float
    bitrate_kbps: float
    transcode_status: str
    size_ratio: float  # newVsOldRatio (100 = same size)
    issues: list[FileIssue] = field(default_factory=list)


def _short_name(path: str) -> str:
    return PurePosixPath(path).stem[:60]


def analyze_file(f: dict, check_processed: bool = True) -> FileReport | None:
    """Analyze a single file record for issues.

    If check_processed is True, only flag post-processing issues
    (missing AAC stereo, unwanted codecs, etc.) on files that have
    already been through the flow.
    """
    file_path = f.get("file") or f.get("_id") or ""
    if not file_path:
        return None

    probe = f.get("ffProbeData") or {}
    streams = probe.get("streams") or []
    if not streams:
        return None

    video_streams = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and s.get("codec_name") not in ("mjpeg", "png", "gif")
    ]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    data_streams = [s for s in streams if s.get("codec_type") == "data"]
    attach_streams = [s for s in streams if s.get("codec_type") == "attachment"]

    vs = video_streams[0] if video_streams else {}
    video_codec = vs.get("codec_name", "?")
    video_tag = vs.get("codec_tag_string", "?")
    resolution = (
        f.get("video_resolution") or f"{vs.get('width', '?')}x{vs.get('height', '?')}"
    )

    container = f.get("container", "?")
    file_size_mb = f.get("file_size", 0)
    bitrate = f.get("bit_rate", 0)
    bitrate_kbps = bitrate / 1000 if bitrate else 0
    transcode_status = f.get("TranscodeDecisionMaker", "") or "unknown"
    size_ratio = f.get("newVsOldRatio", 0) or 0

    is_processed = transcode_status in PROCESSED_STATUSES

    report = FileReport(
        path=file_path,
        name=_short_name(file_path),
        container=container,
        video_codec=video_codec,
        video_tag=video_tag,
        resolution=resolution,
        audio_streams=[
            {
                "codec": s.get("codec_name", "?"),
                "channels": s.get("channels", 0),
                "lang": (s.get("tags") or {}).get("language", "?"),
            }
            for s in audio_streams
        ],
        file_size_mb=file_size_mb,
        bitrate_kbps=bitrate_kbps,
        transcode_status=transcode_status,
        size_ratio=size_ratio,
    )

    # Only flag post-processing issues on files that have been through the flow
    if check_processed and not is_processed:
        return report

    # ── Issue detection ───────────────────────────────────────────────────

    # Container
    if container != "mp4":
        report.issues.append(FileIssue("error", f"Container: {container} (not mp4)"))

    # Video codec
    if video_codec not in ("hevc", "?"):
        report.issues.append(FileIssue("warning", f"Video: {video_codec} (not HEVC)"))

    # hvc1 tag
    if video_codec == "hevc" and video_tag not in ("hvc1", "?"):
        report.issues.append(FileIssue("error", f"HEVC tag: {video_tag} (not hvc1)"))

    # DoVi
    if video_tag and "dv" in video_tag.lower():
        report.issues.append(FileIssue("info", f"DoVi ({video_tag})"))

    # No audio
    if not audio_streams:
        report.issues.append(FileIssue("error", "No audio streams"))

    # Missing AAC stereo
    has_aac_stereo = any(
        s.get("codec_name") == "aac" and s.get("channels", 0) <= 2
        for s in audio_streams
    )
    if audio_streams and not has_aac_stereo:
        report.issues.append(FileIssue("error", "Missing AAC stereo"))

    # Unwanted audio codecs
    for s in audio_streams:
        codec = s.get("codec_name", "")
        ch = s.get("channels", 0)
        lang = (s.get("tags") or {}).get("language", "?")
        if codec in UNWANTED_AUDIO_CODECS:
            report.issues.append(
                FileIssue("error", f"Unwanted audio: {codec} {ch}ch ({lang})")
            )

    # EAC3 with < 6 channels
    for s in audio_streams:
        if s.get("codec_name") == "eac3" and s.get("channels", 0) < 6:
            report.issues.append(
                FileIssue("warning", f"EAC3 {s.get('channels', 0)}ch (redundant)")
            )

    # Subtitles
    if sub_streams:
        codecs = sorted(set(s.get("codec_name", "?") for s in sub_streams))
        report.issues.append(
            FileIssue("warning", f"{len(sub_streams)} subtitle(s): {', '.join(codecs)}")
        )

    # Data/attachment streams
    if data_streams:
        report.issues.append(
            FileIssue("warning", f"{len(data_streams)} data stream(s)")
        )
    if attach_streams:
        report.issues.append(FileIssue("error", f"{len(attach_streams)} attachment(s)"))

    # Size increase
    if size_ratio > 150:
        report.issues.append(
            FileIssue("error", f"Size increase: {size_ratio:.0f}% of original")
        )
    elif size_ratio > 120:
        report.issues.append(
            FileIssue("warning", f"Size increase: {size_ratio:.0f}% of original")
        )

    return report


# ── Output formatting ─────────────────────────────────────────────────────────

SEV = {"error": "X", "warning": "!", "info": "-"}


def _print_search_result(f: dict) -> None:
    """Print detailed info for a single file record (used by --search)."""
    file_path = f.get("_id") or f.get("file") or "?"
    short = PurePosixPath(file_path).name if file_path else "(unknown)"
    probe = f.get("ffProbeData") or {}
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}

    vs = next(
        (s for s in streams if s.get("codec_type") == "video"
         and s.get("codec_name") not in ("mjpeg", "png", "gif")),
        {},
    )
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    data_streams = [s for s in streams if s.get("codec_type") == "data"]
    attach_streams = [s for s in streams if s.get("codec_type") == "attachment"]

    codec = vs.get("codec_name") or f.get("video_codec_name", "?")
    tag = vs.get("codec_tag_string", "?")
    res = (
        f.get("video_resolution")
        or f"{vs.get('width', '?')}x{vs.get('height', '?')}"
    )
    container = f.get("container", "?")
    size_mb = f.get("file_size", 0)
    status = f.get("TranscodeDecisionMaker", "?")
    size_ratio = f.get("newVsOldRatio", 0) or 0
    footprint = f.get("footprintId", "")
    dur_s = _safe_float(fmt.get("duration"))
    br = _safe_float(fmt.get("bit_rate")) / 1000

    audio_desc = (
        ", ".join(
            f"{s.get('codec_name', '?')}/{s.get('channels', '?')}ch"
            f"/{(s.get('tags') or {}).get('language', '?')}"
            for s in audio_streams
        )
        or "none"
    )

    ratio_str = f" | {size_ratio:.0f}% of orig" if size_ratio else ""
    print(f"\n    {short}")
    print(f"      {container} | {codec} ({tag}) | {res} | {br:.0f} kbps | {size_mb:.1f} MB{ratio_str}")
    print(f"      Audio: {audio_desc}")
    print(f"      Status: {status}")
    if footprint:
        print(f"      footprintId: {footprint}")
    if dur_s:
        print(f"      Duration: {dur_s / 60:.1f} min")
    extras = []
    if sub_streams:
        codecs = sorted(set(s.get("codec_name", "?") for s in sub_streams))
        extras.append(f"{len(sub_streams)} sub ({', '.join(codecs)})")
    if data_streams:
        codecs = sorted(set(s.get("codec_tag_string", "?") for s in data_streams))
        extras.append(f"{len(data_streams)} data ({', '.join(codecs)})")
    if attach_streams:
        extras.append(f"{len(attach_streams)} attachments")
    if extras:
        print(f"      Other: {', '.join(extras)}")


def _audio_desc(r: FileReport) -> str:
    return (
        ", ".join(
            f"{a['codec']}/{a['channels']}ch/{a['lang']}" for a in r.audio_streams
        )
        or "none"
    )


def _print_file(r: FileReport) -> None:
    ratio_str = f" | {r.size_ratio:.0f}% of orig" if r.size_ratio else ""
    print(f"\n    {r.name}")
    print(
        f"      {r.container} | {r.video_codec} ({r.video_tag}) | {r.resolution} "
        f"| {r.bitrate_kbps:.0f} kbps | {r.file_size_mb:.1f} MB{ratio_str}"
    )
    print(f"      Audio: {_audio_desc(r)}")
    print(f"      Status: {r.transcode_status}")
    for issue in r.issues:
        print(f"      [{SEV.get(issue.severity, '?')}] {issue.message}")


def print_host_report(reports: list[FileReport], host: str, show_clean: bool) -> dict:
    """Print analysis for one host and return summary counts."""
    processed = [r for r in reports if r.transcode_status in PROCESSED_STATUSES]
    queued = [r for r in reports if r.transcode_status not in PROCESSED_STATUSES]

    errors = [r for r in processed if any(i.severity == "error" for i in r.issues)]
    warnings = [
        r
        for r in processed
        if any(i.severity == "warning" for i in r.issues) and r not in errors
    ]
    clean = [
        r
        for r in processed
        if not any(i.severity in ("error", "warning") for i in r.issues)
    ]

    print(f"\n{'=' * 80}")
    print(f"  {host}")
    print(
        f"  {len(reports)} total files | {len(processed)} processed "
        f"({len(clean)} clean, {len(warnings)} warnings, {len(errors)} errors) "
        f"| {len(queued)} queued"
    )
    print(f"{'=' * 80}")

    if errors:
        print(f"\n  ERRORS ({len(errors)} files)")
        print(f"  {'-' * 76}")
        for r in sorted(errors, key=lambda r: r.name):
            _print_file(r)

    if warnings:
        print(f"\n  WARNINGS ({len(warnings)} files)")
        print(f"  {'-' * 76}")
        for r in sorted(warnings, key=lambda r: r.name):
            _print_file(r)

    if show_clean and clean:
        print(f"\n  CLEAN ({len(clean)} files)")
        print(f"  {'-' * 76}")
        for r in sorted(clean, key=lambda r: r.name):
            _print_file(r)

    # Issue summary
    issue_counts: dict[str, int] = {}
    for r in processed:
        for issue in r.issues:
            # Group by first word(s) before the colon
            key = (
                issue.message.split(":")[0].strip()
                if ":" in issue.message
                else issue.message
            )
            issue_counts[key] = issue_counts.get(key, 0) + 1

    if issue_counts:
        print(f"\n  Issue Summary (processed files only)")
        print(f"  {'-' * 76}")
        for msg, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
            print(f"    {count:>4}x  {msg}")

    return {
        "total": len(reports),
        "processed": len(processed),
        "queued": len(queued),
        "clean": len(clean),
        "warnings": len(warnings),
        "errors": len(errors),
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def _replace_error_files(
    error_files: list[dict],
    host: str,
    host_api_key: str | None,
) -> None:
    """Delete error files from Tdarr and trigger arr replacement searches."""
    arr_config = _get_arr_config()
    arr_info = arr_config.get(host, {})
    arr_type = arr_info.get("arr", "")
    arr_host = arr_info.get("arr_host", "")
    arr_key = arr_info.get("arr_api_key", "")

    print(f"\n  Replace errors ({len(error_files)} files):")
    if arr_type and arr_host and arr_key:
        print(f"  arr: {arr_type} at {arr_host}")
    elif arr_type:
        print(f"  WARNING: {arr_type} config incomplete (missing host or API key), skipping arr search")
        arr_type = ""
    else:
        print("  WARNING: No arr config found for this server, skipping arr search")

    deleted = 0
    skipped = 0
    searched = 0
    for ef in error_files:
        file_id = ef.get("_id") or ef.get("file", "")
        if not file_id:
            skipped += 1
            continue
        short = PurePosixPath(file_id).name

        # Show file info
        probe = ef.get("ffProbeData") or {}
        probe_streams = probe.get("streams") or []
        vs = next(
            (s for s in probe_streams if s.get("codec_type") == "video"),
            {},
        )
        codec = vs.get("codec_name") or ef.get("video_codec_name", "?")
        res = (
            ef.get("video_resolution")
            or f"{vs.get('width', '?')}x{vs.get('height', '?')}"
        )
        size_mb = ef.get("file_size", 0)
        audio = [s for s in probe_streams if s.get("codec_type") == "audio"]

        print(f"\n    {short}")
        print(f"      {codec} {res} | {size_mb:.1f} MB | audio: {len(audio)} streams")

        try:
            answer = (
                input("      Delete and search for replacement? [y/N] ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(1)

        if answer != "y":
            print("      [SKIPPED]")
            skipped += 1
            continue

        # Delete from Tdarr DB (also removes from disk)
        ok, msg = delete_file(host, file_id, host_api_key)
        if ok:
            print("      [DELETED]")
            deleted += 1
        else:
            print(f"      [DELETE FAILED] {msg}")
            continue

        # Trigger arr search
        if not arr_host:
            print("      [!] No arr config — skipping search")
            continue

        try:
            if arr_type == "radarr":
                parsed = _parse_movie(file_id)
                if parsed:
                    title, year = parsed
                    result = arr_search_movie(arr_host, arr_key, title, year)
                    print(f"      Radarr: {result}")
                    searched += 1
                else:
                    print("      [!] Could not parse movie from path")
            elif arr_type == "sonarr":
                parsed = _parse_series_episode(file_id)
                if parsed:
                    series, season, episode = parsed
                    result = arr_search_episode(
                        arr_host, arr_key, series, season, episode
                    )
                    print(f"      Sonarr: {result}")
                    searched += 1
                else:
                    print("      [!] Could not parse episode from path")
        except Exception as e:
            print(f"      [!] Arr search failed: {e}")

    print(
        f"\n  Done: {deleted} deleted, {searched} searches triggered, "
        f"{skipped} skipped"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Tdarr file state for flow issues",
    )
    parser.add_argument(
        "hosts",
        nargs="*",
        metavar="HOST",
        help="Tdarr server URL(s), e.g. http://localhost:<PORT>",
    )
    parser.add_argument("--api-key", default=None, help="Tdarr API key (optional)")
    parser.add_argument(
        "--servers",
        action="store_true",
        help="Read server list from servers.local.json (no HOST args needed)",
    )
    parser.add_argument(
        "--status",
        default="processed",
        choices=["processed", "queued", "errors", "all"],
        help="Which files to analyze (default: processed)",
    )
    parser.add_argument(
        "--clean", action="store_true", help="Also show clean files with no issues"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw file JSON and exit"
    )
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="Requeue files that have errors (set TranscodeDecisionMaker to 'Queued')",
    )
    parser.add_argument(
        "--delete-errors",
        action="store_true",
        help="Delete files that have transcode errors (from disk and DB). "
        "Prompts for confirmation on each file.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Run ffprobe on error files via local SMB mounts to verify actual "
        "stream state on disk. Requires volumes mounted at /Volumes/Movies, "
        "/Volumes/Television, etc.",
    )
    parser.add_argument(
        "--replace-errors",
        action="store_true",
        help="Delete transcode error files from Tdarr DB and disk, then trigger "
        "Radarr/Sonarr search for replacements. Reads arr config from "
        "servers.local.json. Prompts for confirmation on each file.",
    )
    parser.add_argument(
        "--mount-prefix",
        default="/Volumes",
        help="Local mount point prefix (default: /Volumes). Tdarr paths like "
        "/movies/... map to <prefix>/Movies/...",
    )
    parser.add_argument(
        "--search",
        default=None,
        metavar="TERM",
        help="Search for files whose path contains TERM (case-insensitive). "
        "Shows matching files with full details regardless of --status.",
    )
    parser.add_argument(
        "--requeue-file",
        default=None,
        metavar="TERM",
        help="Requeue files whose path contains TERM (case-insensitive). "
        "Shows matches and requeues them.",
    )
    args = parser.parse_args()

    # --search and --requeue-file are not compatible with --json
    if args.json and (args.search or args.requeue_file):
        parser.error("--json cannot be combined with --search or --requeue-file")

    # --requeue and --delete-errors don't work with --status errors
    # (use --replace-errors instead for the error-specific workflow)
    if args.status == "errors" and (args.requeue or args.delete_errors):
        parser.error(
            "--requeue and --delete-errors are not compatible with --status errors. "
            "Use --replace-errors for the error deletion/re-search workflow."
        )

    # Build list of (host, api_key) pairs
    server_entries: list[tuple[str, str | None]] = []
    if args.servers:
        servers_file = Path(__file__).resolve().parent.parent / "servers.local.json"
        if not servers_file.exists():
            print(f"ERROR: {servers_file} not found. Copy servers.local.json.example to get started.")
            sys.exit(1)
        with open(servers_file) as fh:
            servers_data = json.load(fh)
        for srv in servers_data.get("servers", []):
            srv_host = srv["host"].rstrip("/")
            srv_key = srv.get("api_key") or args.api_key
            server_entries.append((srv_host, srv_key))
    elif args.hosts:
        for h in args.hosts:
            server_entries.append((h.rstrip("/"), args.api_key))
    else:
        parser.error("Provide HOST argument(s) or use --servers to read servers.local.json")

    grand = {
        "total": 0,
        "processed": 0,
        "queued": 0,
        "clean": 0,
        "warnings": 0,
        "errors": 0,
    }
    all_json_files: list[dict] = []

    # When outputting JSON, send status messages to stderr so stdout is clean
    _log = (lambda *a, **kw: print(*a, **kw, file=sys.stderr)) if args.json else print

    for host, host_api_key in server_entries:
        _log(f"\nConnecting to {host} ...")

        try:
            status = get_status(host, host_api_key)
            _log(f"  Tdarr v{status.get('version', '?')}")
        except Exception as e:
            _log(f"  ERROR: Cannot connect: {e}")
            continue

        try:
            files = get_all_files(host, host_api_key)
        except Exception as e:
            _log(f"  ERROR: Failed to fetch files: {e}")
            continue

        if not files:
            _log("  No files found.")
            continue

        _log(f"  {len(files)} file records")

        # --search: find files by name and show details
        if args.search:
            term = args.search.lower()
            matches = [
                f for f in files
                if isinstance(f, dict)
                and term in (f.get("_id") or f.get("file") or "").lower()
            ]
            if matches:
                print(f"\n  Search results for '{args.search}' ({len(matches)} matches)")
                print(f"  {'-' * 76}")
                for mf in matches:
                    _print_search_result(mf)
            else:
                print(f"\n  No files matching '{args.search}'")
            continue

        # --requeue-file: find and requeue specific files by name
        if args.requeue_file:
            term = args.requeue_file.lower()
            matches = [
                f for f in files
                if isinstance(f, dict)
                and term in (f.get("_id") or f.get("file") or "").lower()
            ]
            if matches:
                print(f"\n  Requeuing {len(matches)} file(s) matching '{args.requeue_file}':")
                for mf in matches:
                    file_id = mf.get("_id") or mf.get("file", "")
                    if not file_id:
                        print("    [SKIP] (no file ID)")
                        continue
                    short = PurePosixPath(file_id).name if file_id else "(unknown)"
                    status = mf.get("TranscodeDecisionMaker", "?")
                    ok = requeue_file(host, file_id, host_api_key)
                    result = "OK" if ok else "FAILED"
                    print(f"    [{result}] {short} (was: {status})")
            else:
                print(f"\n  No files matching '{args.requeue_file}'")
            continue

        if args.json:
            # Filter by status before dumping
            if args.status == "processed":
                files = [
                    f
                    for f in files
                    if f.get("TranscodeDecisionMaker", "") in PROCESSED_STATUSES
                ]
            elif args.status == "queued":
                files = [
                    f
                    for f in files
                    if f.get("TranscodeDecisionMaker", "") not in PROCESSED_STATUSES
                ]
            elif args.status == "errors":
                files = [
                    f
                    for f in files
                    if f.get("TranscodeDecisionMaker", "") == "Transcode error"
                ]
            all_json_files.extend(files)
            continue

        # For --status errors, show transcode error files with stream details
        if args.status == "errors":
            error_files = [
                f
                for f in files
                if isinstance(f, dict)
                and f.get("TranscodeDecisionMaker") == "Transcode error"
            ]
            print(f"\n  TRANSCODE ERRORS ({len(error_files)} files)")
            print(f"  {'-' * 76}")
            for ef in error_files:
                file_id = ef.get("_id") or ef.get("file", "")
                short = PurePosixPath(file_id).name if file_id else "(unknown)"
                probe = ef.get("ffProbeData") or {}
                probe_streams = probe.get("streams") or []
                fmt = probe.get("format") or {}
                vs = next(
                    (s for s in probe_streams if s.get("codec_type") == "video"),
                    {},
                )
                codec = vs.get("codec_name") or ef.get("video_codec_name", "?")
                tag = vs.get("codec_tag_string", "?")
                res = (
                    ef.get("video_resolution")
                    or f"{vs.get('width', '?')}x{vs.get('height', '?')}"
                )
                size_mb = ef.get("file_size", 0)
                dur_s = _safe_float(fmt.get("duration"))
                dur_min = dur_s / 60 if dur_s else 0
                br = _safe_float(fmt.get("bit_rate")) / 1000
                encoder = fmt.get("tags", {}).get("encoder", "")

                audio_streams = [
                    s for s in probe_streams if s.get("codec_type") == "audio"
                ]
                audio_desc = (
                    ", ".join(
                        f"{s.get('codec_name', '?')}/{s.get('channels', '?')}ch"
                        f"/{(s.get('tags') or {}).get('language', '?')}"
                        for s in audio_streams
                    )
                    or "none"
                )

                footprint = ef.get("footprintId", "")

                print(f"\n    {short}")
                print(
                    f"      {codec} ({tag}) | {res} | {br:.0f} kbps "
                    f"| {size_mb:.1f} MB | {dur_min:.0f} min"
                )
                print(f"      Audio: {audio_desc}")
                if footprint:
                    print(f"      footprintId: {footprint}")
                if encoder:
                    print(f"      Muxer: {encoder}")

                # Flag obvious issues
                if not probe_streams:
                    print("      [X] No streams in ffProbeData (sparse probe)")
                if not audio_streams:
                    print("      [X] No audio streams")
                enc_tag = vs.get("tags", {}).get("encoder", "")
                if enc_tag and ("nvenc" in enc_tag or "libx265" in enc_tag):
                    print(f"      [!] Encoded by flow ({enc_tag})")

                # Local disk probe
                if args.probe and file_id:
                    local_path = _tdarr_path_to_local(
                        file_id, args.mount_prefix
                    )
                    if Path(local_path).exists():
                        disk_probe = probe_file(local_path)
                        if disk_probe:
                            _print_probe_result(disk_probe)
                        else:
                            print(f"      Disk probe: FAILED (ffprobe error)")
                    else:
                        print(f"      Disk probe: file not found at {local_path}")

            # Populate grand totals for --status errors mode
            processed_count = sum(
                1 for f in files if isinstance(f, dict)
                and f.get("TranscodeDecisionMaker", "") in PROCESSED_STATUSES
            )
            queued_count = sum(
                1 for f in files if isinstance(f, dict)
                and f.get("TranscodeDecisionMaker", "") not in PROCESSED_STATUSES
                and f.get("TranscodeDecisionMaker", "") != "Transcode error"
            )
            grand["errors"] += len(error_files)
            grand["processed"] += processed_count
            grand["queued"] += queued_count
            grand["total"] += len(files)

            # --replace-errors within --status errors
            if args.replace_errors and error_files:
                _replace_error_files(error_files, host, host_api_key)

            continue

        check_processed = args.status == "processed"
        reports = []
        for f in files:
            r = analyze_file(f, check_processed=check_processed)
            if r:
                # Filter by requested status
                if (
                    args.status == "processed"
                    and r.transcode_status not in PROCESSED_STATUSES
                ):
                    continue
                if args.status == "queued" and r.transcode_status in PROCESSED_STATUSES:
                    continue
                reports.append(r)

        if not reports:
            print("  No files match the selected status filter.")
            continue

        counts = print_host_report(reports, host, show_clean=args.clean)
        for k in grand:
            grand[k] += counts[k]

        # Requeue files with errors
        if args.requeue:
            error_reports = [
                r for r in reports if any(i.severity == "error" for i in r.issues)
            ]
            if error_reports:
                print(f"\n  Requeuing {len(error_reports)} file(s) with errors...")
                for r in error_reports:
                    file_id = r.path
                    ok = requeue_file(host, file_id, host_api_key)
                    status_str = "OK" if ok else "FAILED"
                    print(f"    [{status_str}] {r.name}")
            else:
                print("\n  No files with errors to requeue.")

        # Delete files with transcode errors (requires per-file confirmation)
        if args.delete_errors:
            error_files = [
                f
                for f in files
                if isinstance(f, dict)
                and f.get("TranscodeDecisionMaker") == "Transcode error"
            ]
            if error_files:
                print(f"\n  Found {len(error_files)} file(s) with transcode errors:")
                deleted = 0
                skipped = 0
                for ef in error_files:
                    file_id = ef.get("_id") or ef.get("file", "")
                    if not file_id:
                        print("\n    (unknown file — no _id or file path, skipping)")
                        skipped += 1
                        continue
                    short = PurePosixPath(file_id).name
                    # Derive display fields from ffProbeData with raw-field fallbacks
                    probe = ef.get("ffProbeData") or {}
                    probe_streams = probe.get("streams") or []
                    vs = next(
                        (s for s in probe_streams if s.get("codec_type") == "video"),
                        {},
                    )
                    codec = vs.get("codec_name") or ef.get("video_codec_name", "?")
                    res = (
                        ef.get("video_resolution")
                        or f"{vs.get('width', '?')}x{vs.get('height', '?')}"
                    )
                    size_mb = ef.get("file_size", 0)
                    print(f"\n    {short}")
                    print(f"      {codec} {res} | {size_mb:.1f} MB")
                    try:
                        answer = input("      Delete from disk and DB? [y/N] ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print("\n  Aborted.")
                        sys.exit(1)
                    if answer == "y":
                        ok, msg = delete_file(host, file_id, host_api_key)
                        if ok:
                            print("      [DELETED]")
                            deleted += 1
                        else:
                            print(f"      [FAILED] {msg}")
                    else:
                        print("      [SKIPPED]")
                        skipped += 1
                print(f"\n  Done: {deleted} deleted, {skipped} skipped")
            else:
                print("\n  No files with transcode errors to delete.")

        # Replace errors: delete from Tdarr + disk, trigger arr search
        if args.replace_errors:
            error_files = [
                f
                for f in files
                if isinstance(f, dict)
                and f.get("TranscodeDecisionMaker") == "Transcode error"
            ]
            if error_files:
                _replace_error_files(error_files, host, host_api_key)
            else:
                print("\n  No files with transcode errors to replace.")

    # Dump combined JSON after processing all servers (single valid array)
    if args.json:
        json.dump(all_json_files, sys.stdout, indent=2)
        print()

    if (
        len(server_entries) > 1
        and not args.json
        and not args.search
        and not args.requeue_file
    ):
        print(f"\n{'=' * 80}")
        print(
            f"  GRAND TOTAL: {grand['total']} files | "
            f"{grand['processed']} processed "
            f"({grand['clean']} clean, {grand['warnings']} warnings, "
            f"{grand['errors']} errors) | {grand['queued']} queued"
        )
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
