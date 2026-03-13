#!/usr/bin/env python3
"""Query Tdarr instances and analyze file state for flow issues.

Usage:
    python3 scripts/analyze_tdarr.py HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --api-key SECRET HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --requeue HOST [HOST ...]
    python3 scripts/analyze_tdarr.py --delete-errors HOST [HOST ...]

Examples:
    python3 scripts/analyze_tdarr.py http://localhost:8265
    python3 scripts/analyze_tdarr.py http://localhost:8265 http://localhost:8265
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
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import PurePosixPath


# ── Tdarr API helpers ─────────────────────────────────────────────────────────


def _post(url: str, payload: dict, api_key: str | None = None) -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
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
        _post(f"{host}/api/v2/cruddb", payload, api_key)
        return True
    except (urllib.error.URLError, Exception):
        return False


def delete_file(
    host: str, file_id: str, api_key: str | None = None
) -> tuple[bool, str]:
    """Delete a file from disk and remove it from the Tdarr database.

    Returns (success, message) so callers can report what went wrong.
    """
    payload = {"data": {"file": {"_id": file_id}}}
    try:
        _post(f"{host}/api/v2/delete-file", payload, api_key)
        return True, "OK"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Connection error: {e.reason}"
    except Exception as e:
        return False, str(e)


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
    if size_ratio > 120:
        report.issues.append(
            FileIssue("warning", f"Size increase: {size_ratio:.0f}% of original")
        )
    elif size_ratio > 150:
        report.issues.append(
            FileIssue("error", f"Size increase: {size_ratio:.0f}% of original")
        )

    return report


# ── Output formatting ─────────────────────────────────────────────────────────

SEV = {"error": "X", "warning": "!", "info": "-"}


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Tdarr file state for flow issues",
    )
    parser.add_argument(
        "hosts",
        nargs="+",
        metavar="HOST",
        help="Tdarr server URL(s), e.g. http://localhost:<PORT>",
    )
    parser.add_argument("--api-key", default=None, help="Tdarr API key (optional)")
    parser.add_argument(
        "--status",
        default="processed",
        choices=["processed", "queued", "all"],
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
    args = parser.parse_args()

    grand = {
        "total": 0,
        "processed": 0,
        "queued": 0,
        "clean": 0,
        "warnings": 0,
        "errors": 0,
    }

    for host in args.hosts:
        host = host.rstrip("/")
        print(f"\nConnecting to {host} ...")

        try:
            status = get_status(host, args.api_key)
            print(f"  Tdarr v{status.get('version', '?')}")
        except Exception as e:
            print(f"  ERROR: Cannot connect: {e}")
            continue

        try:
            files = get_all_files(host, args.api_key)
        except Exception as e:
            print(f"  ERROR: Failed to fetch files: {e}")
            continue

        if not files:
            print("  No files found.")
            continue

        print(f"  {len(files)} file records")

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
            json.dump(files, sys.stdout, indent=2)
            print()
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
                    ok = requeue_file(host, file_id, args.api_key)
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
                        ok, msg = delete_file(host, file_id, args.api_key)
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

    if len(args.hosts) > 1 and not args.json:
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
