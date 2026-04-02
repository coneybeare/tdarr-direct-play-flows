#!/usr/bin/env python3
"""Deploy flow definitions to Tdarr servers.

Reads the local flow JSON and pushes it to each configured server via
the Tdarr V2 cruddb API.  Per-server overrides (e.g. Radarr vs Sonarr
notification settings) are applied before upload.

Local plugins (``sourceRepo: "local"``) are deployed by copying the
``plugins/LocalFlowPlugins/`` directory from this repo to the path
specified by ``plugin_path`` in each server's config entry.  This must
point to the Tdarr server's LocalFlowPlugins directory (typically a
locally-mounted Docker volume).

Configuration
-------------
Copy ``servers.local.json.example`` to ``servers.local.json`` and fill
in your server hosts, flow IDs, API keys, and any per-node overrides.
Set ``plugin_path`` on each server entry to enable plugin deployment.

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
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOW_PATH = ROOT / "flows" / "01_hevc_mp4_direct_play.json"
CONFIG_PATH = ROOT / "servers.local.json"
PLUGINS_DIR = ROOT / "plugins" / "LocalFlowPlugins"


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


def deploy_plugins(server, dry_run=False):
    """Copy local plugins to the server's LocalFlowPlugins directory.

    The ``plugin_path`` field in the server config must point to the
    Tdarr server's LocalFlowPlugins directory (e.g. a Docker volume
    mount).  If the field is absent or empty the step is skipped.

    Returns True on success (or skip), False on error.
    """
    name = server["name"]
    plugin_path = server.get("plugin_path", "").strip()

    if not plugin_path:
        print(f"  Plugins: skipping (no plugin_path configured)")
        return True

    dest = Path(plugin_path)

    if not PLUGINS_DIR.exists():
        print(f"  Plugins: FAILED — source directory not found: {PLUGINS_DIR}")
        return False

    plugin_files = list(PLUGINS_DIR.rglob("*"))
    file_count = sum(1 for p in plugin_files if p.is_file())
    print(f"  Plugins: {file_count} file(s) from {PLUGINS_DIR} -> {dest}")

    if dry_run:
        print("  Plugins: [DRY RUN] Skipping copy")
        return True

    try:
        shutil.copytree(str(PLUGINS_DIR), str(dest), dirs_exist_ok=True)
        print(f"  Plugins: deployed {file_count} file(s) OK")
    except Exception as e:
        print(f"  Plugins: FAILED — {e}")
        return False

    return True


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
