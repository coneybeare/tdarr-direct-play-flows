#!/usr/bin/env python3
"""Shared SSH + Docker helpers for reaching the Tdarr containers.

Several scripts need to run ffprobe/ffmpeg, or inspect the process table, on the
Tdarr hosts. Tdarr ships its own ffmpeg build inside the container, so going
through `docker exec` uses the same binary the transcodes use rather than
whatever happens to be on the host or on this machine.

Requires key-based SSH access to the Tdarr hosts.
"""

from __future__ import annotations

import subprocess
from urllib.parse import urlparse

# Tdarr's bundled ffmpeg/ffprobe inside the Docker container.
DOCKER_FFMPEG = "/app/Tdarr_Node/assets/app/ffmpeg/linux_x64/ffmpeg"
DOCKER_FFPROBE = "/app/Tdarr_Node/assets/app/ffmpeg/linux_x64/ffprobe"
DOCKER_BIN = "/usr/local/bin/docker"
TDARR_CONTAINER = "Tdarr"


def ssh_host_from_tdarr(tdarr_host: str) -> str:
    """Derive an SSH hostname from a Tdarr API URL.

    Accepts a bare hostname unchanged, so callers can pass either form.
    """
    parsed = urlparse(tdarr_host)
    return parsed.hostname or tdarr_host


def shq(s: str) -> str:
    """Shell-quote a string for safe use in SSH commands.

    Library paths routinely contain spaces, quotes and brackets, so every path
    interpolated into a remote command must go through this.
    """
    return "'" + s.replace("'", "'\\''") + "'"


# Hostnames meaning "run here, no SSH". A process scheduled on a Tdarr host
# itself may have no SSH credentials, so it must talk to Docker directly.
LOCAL_HOSTS = {"", "local", "localhost", "127.0.0.1", "::1"}


def is_local(host: str) -> bool:
    return (host or "").strip().lower() in LOCAL_HOSTS


def ssh_run(ssh_host: str, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    """Run a command on a Tdarr host. Returns (rc, stdout, stderr).

    Runs locally when the host is one of LOCAL_HOSTS, otherwise over SSH, so the
    same code works from a workstation and from a Tdarr host itself.
    """
    if is_local(ssh_host):
        argv: list[str] | str = cmd
        shell = True
        missing = "shell not available"
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host, cmd]
        shell = False
        missing = "ssh not found on PATH"
    try:
        result = subprocess.run(
            argv, shell=shell, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", missing


def docker_exec(ssh_host: str, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    """Run a command inside the Tdarr Docker container via SSH."""
    return ssh_run(ssh_host, f"{DOCKER_BIN} exec {TDARR_CONTAINER} {cmd}", timeout)
