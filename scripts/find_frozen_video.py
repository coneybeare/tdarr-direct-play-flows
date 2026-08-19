#!/usr/bin/env python3
"""Find files whose video is a single frozen frame repeated for the whole runtime.

NVDEC on Turing silently fails to decode some sources (confirmed: VC-1 in ASF).
It emits one frame, the encoder duplicates it for the entire duration, and the
result is a structurally valid HEVC/MP4 that plays as a still image with correct
audio. Tdarr's health check passes it, the duration check passes it, and the
flow replaced the original with it.

Detection works in two stages:

  1. Screen  - the Tdarr DB records a video bit_rate far below anything a real
               encode produces at that resolution. Cheap, no file access.
  2. Verify  - decode one frame at several points spread across the runtime and
               compare their brightness/saturation statistics. If the picture is
               identical at 10% and at 90% of the file, it never changes.
               Slower, needs SSH + the Tdarr container, but it is conclusive.

Packet-size variance does NOT work as a signal here: a frozen encode is mostly
tiny duplicate frames punctuated by periodic keyframes of the still image, which
gives it a *higher* coefficient of variation than real content.

Usage:
    python3 scripts/find_frozen_video.py --servers
    python3 scripts/find_frozen_video.py --servers --verify
    python3 scripts/find_frozen_video.py --servers --verify --out frozen.txt
    python3 scripts/find_frozen_video.py --api-key SECRET http://HOST:PORT

Screening only reports suspects. Pass --verify before acting on the results:
a genuinely tiny-but-fine encode (a static-camera clip, a slideshow) screens the
same way and is only ruled out by the frame-comparison check.

This script never modifies or deletes anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyze_tdarr as A  # noqa: E402
import cleanup_stereo_eac3 as C  # noqa: E402

# Minimum plausible video bitrate (bits/sec) for a real encode at a given width.
# The frozen files sit at 10-25 kbps; the lowest legitimate encodes observed in
# a ~29k-file library are two orders of magnitude above these floors.
BITRATE_FLOOR_BY_WIDTH = [
    (1920, 150_000),
    (1280, 100_000),
    (854, 60_000),
    (0, 40_000),
]

# Fractions of the runtime to sample when verifying. Spread wide so a file that
# only breaks partway through is still caught.
SAMPLE_POINTS = (0.10, 0.30, 0.50, 0.70, 0.90)

# Largest spread across samples (in 0-255 luma units) still considered "identical".
# Real footage moves by tens of units between distant points; re-decoding the same
# frame reproduces it bit-for-bit, so the observed spread on frozen files is 0.
FROZEN_SPREAD_THRESHOLD = 0.5


def bitrate_floor(width: int) -> int:
    for min_width, floor in BITRATE_FLOOR_BY_WIDTH:
        if width >= min_width:
            return floor
    return BITRATE_FLOOR_BY_WIDTH[-1][1]


def video_stream(rec: dict) -> dict:
    """First real video stream, skipping embedded cover art."""
    probe = rec.get("ffProbeData") or {}
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        if stream.get("codec_name") in ("mjpeg", "png", "gif", "bmp"):
            continue
        return stream
    return {}


def screen(records: list[dict]) -> list[dict]:
    """Records whose video bitrate is too low to be a real encode."""
    suspects = []
    for rec in records:
        stream = video_stream(rec)
        if not stream:
            continue
        try:
            bitrate = int(stream.get("bit_rate") or 0)
        except (TypeError, ValueError):
            continue
        width = int(stream.get("width") or 0)
        if bitrate <= 0 or width <= 0:
            continue
        if bitrate < bitrate_floor(width):
            suspects.append(
                {
                    "path": rec.get("_id"),
                    "codec": stream.get("codec_name"),
                    "width": width,
                    "bitrate": bitrate,
                    "status": rec.get("TranscodeDecisionMaker"),
                    "ratio": rec.get("newVsOldRatio"),
                }
            )
    suspects.sort(key=lambda s: s["bitrate"])
    return suspects


def _duration(ssh_host: str, path: str) -> float | None:
    cmd = (
        f"{C.DOCKER_FFPROBE} -v error -show_entries format=duration "
        f"-of default=nw=1:nk=1 {C._shq(path)}"
    )
    rc, out, _ = C._docker_exec(ssh_host, f"sh -c {C._shq(cmd)}", timeout=300)
    if rc != 0:
        return None
    try:
        value = float(out.strip().split("\n")[0])
    except (ValueError, IndexError):
        return None
    return value if value > 0 else None


def _frame_signature(ssh_host: str, path: str, offset: float) -> tuple | None:
    """Brightness/saturation of a single frame at `offset` seconds."""
    cmd = (
        f"{C.DOCKER_FFMPEG} -nostdin -hide_banner -ss {offset:.2f} -i {C._shq(path)} "
        f"-frames:v 1 -an -vf signalstats,metadata=print:file=- -f null /dev/null 2>&1 "
        f"| grep -E 'YMIN|YAVG|YMAX|SATAVG'"
    )
    rc, out, _ = C._docker_exec(ssh_host, f"sh -c {C._shq(cmd)}", timeout=300)
    values = []
    for line in out.split("\n"):
        if "=" not in line:
            continue
        try:
            values.append(float(line.rsplit("=", 1)[1].strip()))
        except ValueError:
            continue
    return tuple(values) if len(values) >= 4 else None


def verify(ssh_host: str, path: str) -> tuple[bool | None, str]:
    """Compare frames across the runtime. Returns (is_frozen, detail).

    None means the file could not be probed, not that it is healthy.
    """
    duration = _duration(ssh_host, path)
    if duration is None:
        return None, "could not read duration"

    signatures = []
    for fraction in SAMPLE_POINTS:
        signature = _frame_signature(ssh_host, path, duration * fraction)
        if signature is not None:
            signatures.append(signature)

    if len(signatures) < 2:
        return None, f"only {len(signatures)} frame(s) decoded"

    spreads = [max(col) - min(col) for col in zip(*signatures)]
    spread = max(spreads)
    detail = (
        f"{len(signatures)} frames across {duration / 60:.0f} min, "
        f"max spread {spread:.2f}"
    )
    return spread < FROZEN_SPREAD_THRESHOLD, detail


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find files whose video is a frozen frame repeated for the whole runtime"
    )
    ap.add_argument("hosts", nargs="*", metavar="HOST", help="Tdarr server URL(s)")
    ap.add_argument("--servers", action="store_true", help="Read hosts from servers.local.json")
    ap.add_argument("--api-key", help="Tdarr API key")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Confirm each suspect by comparing frames across the runtime over SSH",
    )
    ap.add_argument("--out", help="Write confirmed (or screened) paths, one per line")
    args = ap.parse_args()

    targets = []
    if args.servers:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "servers.local.json")) as fh:
            for srv in json.load(fh)["servers"]:
                targets.append((srv["name"], srv["host"], srv.get("api_key")))
    for host in args.hosts:
        targets.append((host, host, args.api_key))

    if not targets:
        ap.error("no hosts given; pass HOST or --servers")

    confirmed: list[str] = []
    for name, host, key in targets:
        print(f"\nConnecting to {host} ...")
        try:
            records = A.get_all_files(host, key)
        except Exception as exc:  # noqa: BLE001 - report and continue to next host
            print(f"  failed: {exc}")
            continue

        suspects = screen(records)
        print(f"  {len(records)} records, {len(suspects)} screened as suspect")
        if not suspects:
            continue

        ssh_host = C._ssh_host_from_tdarr(host)
        for suspect in suspects:
            line = (
                f"    {suspect['bitrate']:>8} bps {suspect['width']:>5}px "
                f"{str(suspect['codec']):>5}  {suspect['path']}"
            )
            if not args.verify:
                print(line)
                confirmed.append(suspect["path"])
                continue

            frozen, detail = verify(ssh_host, suspect["path"])
            if frozen is None:
                mark = "SKIP  "
            elif frozen:
                mark = "FROZEN"
                confirmed.append(suspect["path"])
            else:
                mark = "ok    "
            print(f"{line}\n           -> {mark} ({detail})")

    label = "confirmed frozen" if args.verify else "screened suspect"
    print(f"\n{len(confirmed)} file(s) {label}.")
    if args.out and confirmed:
        with open(args.out, "w") as fh:
            for path in confirmed:
                fh.write(path + "\n")
        print(f"Wrote {args.out}")
    if not args.verify:
        print("Re-run with --verify before acting on these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
