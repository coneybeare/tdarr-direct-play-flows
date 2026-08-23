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

Nor do YMIN/YMAX: they are integer extremes over millions of pixels and still
wobble by +/-1 on a frozen picture. Deciding on the largest spread across all
metrics let that wobble clear the threshold and pass genuinely frozen files.
Only the frame-wide averages decide. Run --self-test to check that logic.

Usage:
    python3 scripts/find_frozen_video.py --servers
    python3 scripts/find_frozen_video.py --servers --verify
    python3 scripts/find_frozen_video.py --servers --verify --out frozen.txt
    python3 scripts/find_frozen_video.py --api-key SECRET http://HOST:PORT
    python3 scripts/find_frozen_video.py --self-test

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
import tdarr_ssh as T  # noqa: E402

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
# Real footage moves by tens of units between distant points.
FROZEN_SPREAD_THRESHOLD = 0.5

# Only the frame-wide averages are used to decide. YMIN and YMAX are integer
# extremes over millions of pixels: on a frozen picture they still wobble by +/-1
# between decodes, and judging on the largest spread across all metrics let that
# wobble mask genuinely frozen files (observed: YAVG/YMAX/SATAVG identical to
# three decimal places while YMIN moved 16 -> 15 -> 16, scoring 1.00 and passing).
DECIDING_METRICS = ("YAVG", "SATAVG")


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
        f"{T.DOCKER_FFPROBE} -v error -show_entries format=duration "
        f"-of default=nw=1:nk=1 {T.shq(path)}"
    )
    rc, out, _ = T.docker_exec(ssh_host, f"sh -c {T.shq(cmd)}", timeout=300)
    if rc != 0:
        return None
    try:
        value = float(out.strip().split("\n")[0])
    except (ValueError, IndexError):
        return None
    return value if value > 0 else None


def _frame_signature(ssh_host: str, path: str, offset: float) -> dict | None:
    """Brightness/saturation of a single frame at `offset` seconds, by metric name."""
    cmd = (
        f"{T.DOCKER_FFMPEG} -nostdin -hide_banner -ss {offset:.2f} -i {T.shq(path)} "
        f"-frames:v 1 -an -vf signalstats,metadata=print:file=- -f null /dev/null 2>&1 "
        f"| grep -E 'YMIN|YAVG|YMAX|SATAVG'"
    )
    rc, out, _ = T.docker_exec(ssh_host, f"sh -c {T.shq(cmd)}", timeout=300)
    values: dict[str, float] = {}
    for line in out.split("\n"):
        if "=" not in line:
            continue
        key, _, raw = line.rpartition("=")
        name = key.rsplit(".", 1)[-1].strip()
        try:
            values[name] = float(raw.strip())
        except ValueError:
            continue
    # Parse by name rather than position: relying on ffmpeg's print order would
    # silently mis-assign metrics if that order ever changed.
    return values if all(m in values for m in DECIDING_METRICS) else None


def decide(signatures: list[dict]) -> tuple[bool, dict]:
    """Frozen or not, from frame signatures. Pure, so it can be tested offline."""
    spreads = {
        m: max(sig[m] for sig in signatures) - min(sig[m] for sig in signatures)
        for m in DECIDING_METRICS
    }
    return max(spreads.values()) < FROZEN_SPREAD_THRESHOLD, spreads


def _self_test() -> int:
    """Regression cases taken from real files, including the one that got away."""
    cases = [
        # The false negative this check exists for: the picture is identical to
        # three decimals, but YMIN wobbled 16 -> 15 -> 16. Judging on the largest
        # spread across all metrics scored that 1.00 and cleared the threshold,
        # leaving genuinely frozen files on disk through two full scans.
        ("frozen, YMIN wobbles by 1", [
            {"YMIN": 16.0, "YAVG": 72.0, "YMAX": 129.0, "SATAVG": 181.0},
            {"YMIN": 15.0, "YAVG": 71.996, "YMAX": 129.0, "SATAVG": 181.0},
            {"YMIN": 16.0, "YAVG": 72.0, "YMAX": 129.0, "SATAVG": 181.0},
        ], True),
        ("frozen, bit-identical", [
            {"YMIN": 16.0, "YAVG": 72.0, "YMAX": 129.0, "SATAVG": 181.0},
            {"YMIN": 16.0, "YAVG": 72.0, "YMAX": 129.0, "SATAVG": 181.0},
        ], True),
        ("healthy live action", [
            {"YMIN": 12.0, "YAVG": 90.1742, "YMAX": 247.0, "SATAVG": 7.50073},
            {"YMIN": 15.0, "YAVG": 74.7297, "YMAX": 239.0, "SATAVG": 9.4826},
            {"YMIN": 11.0, "YAVG": 78.4495, "YMAX": 247.0, "SATAVG": 8.4326},
        ], False),
        ("healthy animation, low bitrate", [
            {"YMIN": 0.0, "YAVG": 20.0, "YMAX": 255.0, "SATAVG": 5.0},
            {"YMIN": 0.0, "YAVG": 148.0, "YMAX": 255.0, "SATAVG": 186.0},
        ], False),
        # A static shot still breathes: compression noise moves the frame mean
        # far more than a duplicated frame does.
        ("near-static but real", [
            {"YMIN": 16.0, "YAVG": 72.0, "YMAX": 129.0, "SATAVG": 181.0},
            {"YMIN": 16.0, "YAVG": 73.2, "YMAX": 129.0, "SATAVG": 181.0},
        ], False),
    ]
    failures = 0
    for name, signatures, expected in cases:
        got, spreads = decide(signatures)
        ok = got is expected
        failures += 0 if ok else 1
        detail = ", ".join(f"{m} {v:.3f}" for m, v in spreads.items())
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: frozen={got} ({detail})")
    print("self-test passed" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


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

    frozen, spreads = decide(signatures)
    detail = (
        f"{len(signatures)} frames across {duration / 60:.0f} min, "
        + ", ".join(f"{m} spread {v:.3f}" for m, v in spreads.items())
    )
    return frozen, detail


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
    ap.add_argument("--self-test", action="store_true",
                    help="Check the frozen/not decision against known real-world cases and exit")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

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

        ssh_host = T.ssh_host_from_tdarr(host)
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
