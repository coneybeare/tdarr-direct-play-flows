#!/usr/bin/env python3
"""Deploy flow definitions to Tdarr servers.

Reads the local flow JSON and pushes it to each configured server via
the Tdarr V2 cruddb API.  Per-server overrides (e.g. Radarr vs Sonarr
notification settings) are applied before upload.

Local plugins (``sourceRepo: "Local"``) are deployed by streaming each
file over SSH into the Tdarr container on the remote server.  For each
server, two paths inside the container are populated:

1. ``/app/server/Tdarr/Plugins/FlowPlugins/LocalFlowPlugins/`` — the
   persistent plugin store, backed by the /app/server host-mount.
   Plugins here survive container restarts.
2. ``/app/Tdarr_Node/assets/app/plugins/FlowPlugins/LocalFlowPlugins/``
   — the node-internal runtime copy that Tdarr Node actually executes
   at job time.  Not persistent, but required for immediate use without
   waiting for a container restart.

Plugins are organised under category subdirectories (e.g. ``file/``,
``tools/``) matching the CommunityFlowPlugins layout.  The source repo
under ``plugins/LocalFlowPlugins/`` mirrors this structure.

Configuration
-------------
Copy ``servers.local.json.example`` to ``servers.local.json`` and fill
in your server hosts, flow IDs, API keys, and any per-node overrides.
Optional per-server plugin-deploy fields:
- ``ssh_host`` — override the SSH target (default: hostname of ``host``)
- ``container_name`` — override the Docker container name (default: ``Tdarr``)
- ``plugin_path`` — override the node-internal plugin path
  (default: ``/app/Tdarr_Node/assets/app/plugins/FlowPlugins/LocalFlowPlugins``)

Usage
-----
    python3 scripts/deploy_flow.py                   # deploy plugins + flow to all servers
    python3 scripts/deploy_flow.py --server "Server A"  # deploy to one server
    python3 scripts/deploy_flow.py --dry-run          # show what would change
    python3 scripts/deploy_flow.py --no-plugins       # skip plugin deployment
    python3 scripts/deploy_flow.py --plugins-only     # deploy only plugins, skip flow
"""
import argparse
import copy
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOW_PATH = ROOT / "flows" / "01_hevc_mp4_direct_play.json"
CONFIG_PATH = ROOT / "servers.local.json"
PLUGINS_DIR = ROOT / "plugins" / "LocalFlowPlugins"

# Default paths inside the Tdarr container.
#
# Tdarr keeps local plugins in two places and we must populate both:
#
# 1. SERVER_PLUGIN_PATH is the persistent server store — backed by the
#    /app/server host-mount, it survives container restarts.  On startup,
#    Tdarr Node rebuilds its own internal plugin tree from this path.
#    Writing here ensures the plugin will be present after any restart.
#
# 2. NODE_PLUGIN_PATH is the node-internal runtime copy that Tdarr Node
#    actually executes at job time.  It is NOT persistent across
#    container restarts.  Writing here makes the plugin available to
#    jobs immediately without needing to restart the container.
#
# Plugins live under category subdirectories (e.g. file/, tools/) in
# both paths, matching how CommunityFlowPlugins are organised.  The
# source repo mirrors this layout under plugins/LocalFlowPlugins/.
SERVER_PLUGIN_PATH = "/app/server/Tdarr/Plugins/FlowPlugins/LocalFlowPlugins"
NODE_PLUGIN_PATH = "/app/Tdarr_Node/assets/app/plugins/FlowPlugins/LocalFlowPlugins"
DEFAULT_CONTAINER_NAME = "Tdarr"
# Synology/DSM: docker binary lives under /usr/local/bin which is not on
# the default SSH PATH.  Prefix the remote command so it's always found.
SSH_ENV_PREFIX = "PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _post(url, payload, api_key=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode()
    return resp.status, body


def _ssh_host_from_url(host_url):
    """Derive an SSH hostname from a Tdarr API URL."""
    parsed = urllib.parse.urlparse(host_url)
    return parsed.hostname or host_url


def _stream_file_to_container(local_path, container, remote_path, ssh_host):
    """Stream a local file into a path inside a Docker container on a
    remote host via SSH + ``docker exec -i``.

    The remote directory is created if missing.  Raises subprocess errors
    on failure.
    """
    remote_dir = remote_path.rsplit("/", 1)[0]
    # Build a single remote shell command that makes the directory and
    # writes the file from stdin.  Single-quote the paths to tolerate
    # spaces, and escape any embedded single quotes in the path itself.
    def shq(s):
        return "'" + s.replace("'", "'\"'\"'") + "'"

    remote_cmd = (
        f"{SSH_ENV_PREFIX} docker exec -i {shq(container)} "
        f"bash -c \"mkdir -p {shq(remote_dir)} && "
        f"cat > {shq(remote_path)}\""
    )
    with open(local_path, "rb") as fh:
        subprocess.run(
            ["ssh", ssh_host, remote_cmd],
            stdin=fh,
            check=True,
            capture_output=True,
        )


def deploy_plugins(server, dry_run=False):
    """Deploy local flow plugins into the Tdarr container on the remote
    server via SSH + ``docker exec``.

    Writes each file to two paths inside the container:
      - The persistent server store at ``/app/server/Tdarr/Plugins/...``
        (backed by the /app/server host-mount; survives restarts)
      - The node-internal runtime path at ``/app/Tdarr_Node/assets/...``
        (not persistent, but required for the plugin to be usable
        immediately without waiting for a container restart)

    Server config fields (all optional):
        ssh_host        SSH target hostname (default: hostname of `host`)
        container_name  Docker container name (default: "Tdarr")
        plugin_path     Override node-internal plugin path

    Returns True on success or skip, False on error.
    """
    name = server["name"]

    if not PLUGINS_DIR.exists():
        print(f"  Plugins: FAILED — source directory not found: {PLUGINS_DIR}")
        return False

    plugin_files = [p for p in PLUGINS_DIR.rglob("*") if p.is_file()]
    if not plugin_files:
        print("  Plugins: skipping (no local plugin files to deploy)")
        return True

    ssh_host = server.get("ssh_host") or _ssh_host_from_url(server["host"])
    container = server.get("container_name") or DEFAULT_CONTAINER_NAME
    node_path = server.get("plugin_path") or NODE_PLUGIN_PATH
    server_path = SERVER_PLUGIN_PATH

    print(
        f"  Plugins: {len(plugin_files)} file(s) -> {ssh_host}:{container}"
    )
    print(f"    Persistent:  {server_path}")
    print(f"    Runtime:     {node_path}")

    if dry_run:
        print("  Plugins: [DRY RUN] Skipping copy")
        return True

    ok = 0
    failed = 0
    for local_file in plugin_files:
        rel = local_file.relative_to(PLUGINS_DIR).as_posix()
        for base in (server_path, node_path):
            remote_file = f"{base}/{rel}"
            try:
                _stream_file_to_container(
                    local_file, container, remote_file, ssh_host
                )
                ok += 1
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode(errors="replace").strip()
                print(f"    FAIL {rel} -> {base}: {stderr or e}")
                failed += 1
            except Exception as e:
                print(f"    FAIL {rel} -> {base}: {e}")
                failed += 1

    total = len(plugin_files) * 2
    if failed == 0:
        print(f"  Plugins: deployed {ok}/{total} writes OK")
        return True
    print(f"  Plugins: FAILED — {failed}/{total} writes failed")
    return False


def apply_overrides(flow, overrides):
    """Patch plugin inputsDB values from override dict.

    ``overrides`` maps plugin IDs to dicts of inputsDB key/value pairs.
    """
    plugin_map = {p["id"]: p for p in flow["flowPlugins"]}
    applied = []
    for node_id, values in overrides.items():
        if node_id not in plugin_map:
            print(f"  WARNING: override target '{node_id}' not found in flow")
            continue
        inputs = plugin_map[node_id]["inputsDB"]
        for key, val in values.items():
            old = inputs.get(key, "<unset>")
            inputs[key] = val
            applied.append(f"    {node_id}.{key}: {old!r} -> {val!r}")
    return applied


def deploy_server(server, flow_data, dry_run=False):
    name = server["name"]
    host = server["host"].rstrip("/")
    flow_id = server["flow_id"]
    api_key = server.get("api_key")
    overrides = server.get("overrides", {})

    print(f"  Flow ID: {flow_id}")

    # Deep copy so overrides don't bleed between servers
    flow = copy.deepcopy(flow_data)

    if overrides:
        changes = apply_overrides(flow, overrides)
        if changes:
            print("  Overrides:")
            for c in changes:
                print(c)
    else:
        print("  No overrides")

    if dry_run:
        print("  [DRY RUN] Skipping deploy")
        return True

    # Deploy
    payload = {
        "data": {
            "collection": "FlowsJSONDB",
            "mode": "update",
            "docID": flow_id,
            "obj": {
                "flowPlugins": flow["flowPlugins"],
                "flowEdges": flow["flowEdges"],
            },
        }
    }

    try:
        status, _ = _post(f"{host}/api/v2/cruddb", payload, api_key)
        print(f"  Deploy: HTTP {status}")
    except (urllib.error.URLError, Exception) as e:
        print(f"  Deploy FAILED: {e}")
        return False

    # Verify
    try:
        verify_payload = {
            "data": {
                "collection": "FlowsJSONDB",
                "mode": "getById",
                "docID": flow_id,
            }
        }
        status, body = _post(f"{host}/api/v2/cruddb", verify_payload, api_key)
        remote = json.loads(body)
        remote_plugins = len(remote.get("flowPlugins", []))
        remote_edges = len(remote.get("flowEdges", []))
        local_plugins = len(flow["flowPlugins"])
        local_edges = len(flow["flowEdges"])

        if remote_plugins == local_plugins and remote_edges == local_edges:
            print(f"  Verify: OK ({remote_plugins} plugins, {remote_edges} edges)")
        else:
            print(f"  Verify: MISMATCH — local ({local_plugins}p/{local_edges}e) "
                  f"vs remote ({remote_plugins}p/{remote_edges}e)")
            return False

        # Spot-check overrides applied
        remote_map = {p["id"]: p for p in remote["flowPlugins"]}
        for node_id, values in overrides.items():
            if node_id in remote_map:
                for key, val in values.items():
                    actual = remote_map[node_id]["inputsDB"].get(key)
                    if actual != val:
                        print(f"  Verify: MISMATCH on {node_id}.{key}: "
                              f"expected {val!r}, got {actual!r}")
                        return False
        if overrides:
            print("  Verify: overrides confirmed")

    except Exception as e:
        print(f"  Verify FAILED: {e}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Deploy flow to Tdarr servers")
    parser.add_argument("--server", help="Deploy to a specific server by name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show overrides without deploying")
    plugin_group = parser.add_mutually_exclusive_group()
    plugin_group.add_argument("--no-plugins", action="store_true",
                              help="Skip plugin deployment, deploy flow only")
    plugin_group.add_argument("--plugins-only", action="store_true",
                              help="Deploy only plugins, skip flow deployment")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help="Path to servers config JSON")
    parser.add_argument("--flow", type=Path, default=FLOW_PATH,
                        help="Path to flow JSON file")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"Config not found: {args.config}")
        print(f"Copy servers.local.json.example to servers.local.json and fill in your values.")
        sys.exit(1)

    if not args.flow.exists():
        print(f"Flow file not found: {args.flow}")
        sys.exit(1)

    with open(args.config) as f:
        config = json.load(f)

    with open(args.flow) as f:
        flow_data = json.load(f)

    print(f"Flow: {args.flow.name} ({len(flow_data['flowPlugins'])} plugins, "
          f"{len(flow_data['flowEdges'])} edges)")

    servers = config.get("servers", [])
    if args.server:
        servers = [s for s in servers if s["name"] == args.server]
        if not servers:
            print(f"Server '{args.server}' not found in config")
            sys.exit(1)

    results = []
    for server in servers:
        print(f"\n{'=' * 40}")
        print(f"  {server['name']}  ({server['host']})")
        print(f"{'=' * 40}")

        ok = True

        if not args.no_plugins:
            ok = deploy_plugins(server, dry_run=args.dry_run) and ok

        if not args.plugins_only:
            ok = deploy_server(server, flow_data, dry_run=args.dry_run) and ok

        results.append((server["name"], ok))

    print(f"\n{'=' * 40}")
    print("  Summary")
    print(f"{'=' * 40}")
    for name, ok in results:
        status = "OK" if ok else "FAILED"
        print(f"  {name}: {status}")

    if not all(ok for _, ok in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
