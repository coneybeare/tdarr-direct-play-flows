#!/usr/bin/env python3
"""Kill Tdarr transcode jobs whose output has stopped growing.

A job can occupy a worker slot indefinitely without failing. The clearest case
seen so far is a source with no usable duration, where constant-frame-rate
conversion pads the timeline with duplicate frames -- but the point of this
script is that it does not care *why*. It watches one thing: is the output file
still growing? One such job ran for 152 hours at 77% CPU before anyone noticed.

Killing the FFmpeg process is the whole action. Tdarr notices the worker died
and requeues the file on its own. This script never touches library files.

Deliberately does not use the Tdarr API -- /api/v2/get-nodes is prone to timing
out on a busy server, and the process table is both cheaper and more direct.

Usage:
    python3 scripts/watch_stalled_workers.py --servers --once --dry-run
    python3 scripts/watch_stalled_workers.py --servers --once
    python3 scripts/watch_stalled_workers.py --servers            # run forever
    python3 scripts/watch_stalled_workers.py --servers --stall-minutes 30

Start with --once --dry-run to see what it would do.

Requires SSH access to the Tdarr hosts (key-based) and the Tdarr Docker
container, same as cleanup_stereo_eac3.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cleanup_stereo_eac3 as C  # noqa: E402

# A job must run at least this long before it is eligible to be killed, so a
# slow-starting encode is never mistaken for a stalled one.
MIN_AGE_SECONDS = 600

PS_COMMAND = "ps -eo pid,etimes,args | grep tdarr-ffmpeg | grep -v grep"

# Library paths routinely contain spaces, so neither the source nor the output
# can be found by splitting on whitespace.
#
# Output: Tdarr writes to its cache directory, and it is the last argument. The
# greedy prefix forces the match to the LAST "/cache/" on the line.
OUTPUT_CACHE_RE = re.compile(r"^.*\s(/cache/.+)$")
# Fallback for a non-default cache location: the last argument that looks like a
# path ending in a media extension.
MEDIA_EXT = r"(?:mp4|mkv|avi|mov|m4v|ts|m2ts|wmv|mpg|mpeg|webm|flv)"
OUTPUT_ANY_RE = re.compile(rf"^.*\s(/.+\.{MEDIA_EXT})$", re.IGNORECASE)
# Source: everything after "-i " up to a media extension that is followed by a
# flag, so " - " inside a title does not truncate it.
SOURCE_RE = re.compile(rf"-i\s+(/.+?\.{MEDIA_EXT})(?=\s+-)", re.IGNORECASE)


class Job:
    """One running FFmpeg process and the output file it is writing."""

    def __init__(self, host: str, pid: str, age: int, args: str):
        self.host = host
        self.pid = pid
        self.age = age
        self.args = args
        self.output = self._tail_path(args)
        self.source = self._source(args)

    @staticmethod
    def _tail_path(args: str) -> str | None:
        line = args.rstrip()
        for pattern in (OUTPUT_CACHE_RE, OUTPUT_ANY_RE):
            m = pattern.match(line)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _source(args: str) -> str:
        m = SOURCE_RE.search(args)
        return m.group(1).strip() if m else "?"

    @property
    def key(self) -> tuple[str, str]:
        return (self.host, self.pid)

    def __repr__(self) -> str:
        return f"{self.host}:{self.pid}"


def list_jobs(host: str) -> list[Job]:
    """Running tdarr-ffmpeg processes on one host."""
    rc, out, _ = C._docker_exec(host, "sh -c " + C._shq(PS_COMMAND), timeout=300)
    if rc != 0:
        return []
    jobs = []
    for line in out.strip().split("\n"):
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        job = Job(host, m.group(1), int(m.group(2)), m.group(3))
        if job.output:
            jobs.append(job)
    return jobs


def output_sizes(host: str, jobs: list[Job]) -> dict[str, int]:
    """Current size of each job's output file. Missing files report -1."""
    if not jobs:
        return {}
    parts = [
        f'printf "%s " {C._shq(j.output)}; (stat -c %s {C._shq(j.output)} 2>/dev/null || echo -1)'
        for j in jobs
    ]
    rc, out, _ = C._docker_exec(host, "sh -c " + C._shq("; ".join(parts)), timeout=300)
    sizes: dict[str, int] = {}
    if rc != 0:
        return sizes
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        path, _, size = line.rpartition(" ")
        try:
            sizes[path.strip()] = int(size)
        except ValueError:
            continue
    return sizes


def kill(host: str, pid: str) -> bool:
    rc, _, _ = C._docker_exec(host, "sh -c " + C._shq(f"kill -9 {pid}"), timeout=180)
    return rc == 0


def sweep(hosts: list[tuple[str, str]], state: dict, stall_seconds: int,
          dry_run: bool) -> int:
    """One pass. Returns how many jobs were killed (or would have been)."""
    killed = 0
    for name, host in hosts:
        jobs = list_jobs(host)
        sizes = output_sizes(host, jobs)
        live = set()

        for job in jobs:
            live.add(job.key)
            size = sizes.get(job.output, -1)
            prev = state.get(job.key)

            if prev is None or prev["size"] != size:
                # First sighting, or it grew: reset the clock.
                state[job.key] = {"size": size, "since": time.time()}
                continue

            stalled_for = time.time() - prev["since"]
            if job.age < MIN_AGE_SECONDS or stalled_for < stall_seconds:
                continue

            action = "WOULD KILL" if dry_run else "KILLING"
            print(
                f"  {action} {name} pid={job.pid} "
                f"age={job.age / 3600:.1f}h stalled={stalled_for / 60:.0f}m "
                f"size={size}\n      source: {job.source}",
                flush=True,
            )
            killed += 1
            if not dry_run and kill(host, job.pid):
                state.pop(job.key, None)

        # Forget jobs that are no longer running.
        for key in [k for k in state if k[0] == host and k not in live]:
            state.pop(key, None)
    return killed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Kill Tdarr transcode jobs whose output has stopped growing"
    )
    ap.add_argument("hosts", nargs="*", metavar="HOST", help="Tdarr server URL(s) or hostname(s)")
    ap.add_argument("--servers", action="store_true", help="Read hosts from servers.local.json")
    ap.add_argument("--stall-minutes", type=float, default=15,
                    help="Kill after the output has not grown for this long (default 15)")
    ap.add_argument("--interval", type=float, default=300,
                    help="Seconds between sweeps (default 300)")
    ap.add_argument("--once", action="store_true", help="Run a single sweep and exit")
    ap.add_argument("--dry-run", action="store_true", help="Report without killing anything")
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []
    if args.servers:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "servers.local.json")) as fh:
            for srv in json.load(fh)["servers"]:
                targets.append((srv["name"], C._ssh_host_from_tdarr(srv["host"])))
    for host in args.hosts:
        targets.append((host, C._ssh_host_from_tdarr(host)))

    if not targets:
        ap.error("no hosts given; pass HOST or --servers")

    stall_seconds = args.stall_minutes * 60

    # A single sweep can only observe one size per job, so it cannot conclude
    # anything about growth. Take a second sample after a short pause.
    if args.once:
        state: dict = {}
        print(f"sweep 1/2 (sampling)  hosts={[t[0] for t in targets]}")
        sweep(targets, state, stall_seconds, args.dry_run)
        wait = min(args.interval, 120)
        print(f"waiting {wait:.0f}s to see which outputs grow...")
        time.sleep(wait)
        # Anything that did not grow across the pause has been stalled at least
        # as long as it has been running, so judge against the elapsed pause.
        print("sweep 2/2 (judging)")
        n = sweep(targets, state, min(stall_seconds, wait), args.dry_run)
        print(f"\n{n} stalled job(s) {'identified' if args.dry_run else 'killed'}")
        return 0

    print(f"watching {[t[0] for t in targets]} "
          f"(stall={args.stall_minutes}m, interval={args.interval}s"
          f"{', DRY RUN' if args.dry_run else ''})")
    state = {}
    while True:
        try:
            n = sweep(targets, state, stall_seconds, args.dry_run)
            if n:
                print(f"  -> {n} job(s) {'flagged' if args.dry_run else 'killed'}", flush=True)
        except Exception as exc:  # noqa: BLE001 - a bad sweep must not end the watch
            print(f"  sweep error (continuing): {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
