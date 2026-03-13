# Claude Code Instructions

## Project Purpose

This project provides a set of Tdarr flows optimized for direct play of HEVC/MP4 files with stereo AAC 2.0 and EAC3 5.1 surround sound. It includes GPU-accelerated encoding with NVENC, software fallback, health checks, duration validation, and optional Plex/arr notifications.

- Primary objective: Ensure optimal direct play compatibility on iOS/Mac/Plex/Oculus Rift/video.js clients and maintain high quality for HEVC/MP4 media files.

## Git Workflow — CRITICAL

**Never push directly to `main`.** All changes must go through a pull request.

When making changes:

1. Check the current branch: `git branch`
2. If on `main`, create a feature branch first: `git checkout -b claude/<description>`
3. Commit to the feature branch
4. Push the branch: `git push -u origin claude/<description>`
5. Open a PR: `gh pr create`

The `main` branch has protection rules enforced on all users (including admins). Direct pushes will be rejected by both the local pre-push hook (`.githooks/pre-push`) and GitHub.

## Running Tests

```
npm test
```

## Updating Flow Layout / SVG

After structural changes to `flows/01_hevc_mp4_direct_play.json`, regenerate positions and the SVG diagram:

```
python3 scripts/layout_flows.py
```

Then commit both the updated flow JSON and `images/01_hevc_mp4_direct_play.svg`.

## Deploying Flows to Tdarr Servers

After merging a PR, deploy the updated flow to all Tdarr servers:

```
python3 scripts/deploy_flow.py
```

Options:
- `--dry-run` — show overrides without deploying
- `--server "Server A"` — deploy to a single server

The script reads `servers.local.json` (gitignored) for server hosts, flow IDs, and per-server overrides (e.g. Radarr vs Sonarr notification config). Copy `servers.local.json.example` to get started.

## PR Review Workflow

- After creating a PR, always check for and address Copilot/reviewer comments before considering work done.
- Use `gh api --paginate repos/{owner}/{repo}/pulls/{num}/comments` to fetch inline review comments.
- Address valid feedback with a follow-up commit on the same branch.
- When asked to review a PR, provide thorough analysis: trace logic through the flow, verify edge cases, check for regressions, and include scenario analysis.

## Tdarr Flow Domain Rules

Key constraints that prevent common mistakes:

- All `inputsDB` values must be **strings** (even booleans and numbers).
- `setFlowVariable` uses short names (e.g. `auto_accept`); `checkFlowVariable` requires the full `args.variables.user.` prefix (e.g. `args.variables.user.auto_accept`).
- `failFlow` throws → engine sets `flowFailed=true` → caught by `onFlowError`. **NEVER** put `failFlow` inside the `onFlowError` chain (infinite loop).
- `requireReview` is auto-approvable by Tdarr's global "auto-approve successful transcodes" setting; use `failFlow` for non-bypassable guards.
- Plugin name typos are official and must be used as-is: `ffmpegCommandRorderStreams` (not Reorder), `ffmpegCommandSetVdeoResolution` (not Video).
- After adding/removing nodes or edges, run `python3 scripts/layout_flows.py` **and** update `col_map_01()` in the script for any new node IDs.
- `sourceHandle "1"` = yes/pass, `"2"` = no/fail.
- All FFmpeg command plugins (`ffmpegCommand*`) build a single command executed by `ffmpegCommandExecute` — ordering within the pipeline matters.

## Code Review Approach

- Trace logic through the full flow path, not just the changed nodes.
- Verify both normal and VR paths are updated consistently when a change applies to both.
- Check that new nodes are added to `col_map_01()` in `scripts/layout_flows.py`.
- Validate edge wiring: every node should have incoming edges (except `inputFile` and `onFlowError`).
- Consider the interaction between FFmpeg command-build plugins (they share state within a single pipeline).

## Tdarr V2 API Reference

All DB operations go through `POST /api/v2/cruddb`. The payload is always `{"data": {...}}`.

### Query all files

```json
{
  "data": {
    "collection": "FileJSONDB",
    "mode": "getAll",
    "docFilter": {}
  }
}
```

- Returns a **dict** keyed by file path (`{"/path/to/file.mp4": {...}, ...}`), not a list.
- `docFilter` is broken server-side — always returns all files regardless of filter. Filter client-side.
- File `_id` field equals the file path (same as the dict key).

### Update a file record

```json
{
  "data": {
    "collection": "FileJSONDB",
    "mode": "update",
    "docID": "<file _id>",
    "obj": { "TranscodeDecisionMaker": "Queued" }
  }
}
```

**CRITICAL:** The field payload key must be `obj`, NOT `update`. Using `update` returns HTTP 200 but silently ignores the change. Reference: <https://github.com/HaveAGitGat/Tdarr/issues/752>

### Other endpoints

- `GET /api/v2/status` — returns `{version, uptime, os, isProduction, buildDate}`.

### Analyzing and requeuing files

```bash
# Analyze processed files on one or more servers
python3 scripts/analyze_tdarr.py http://HOST:PORT

# Analyze and requeue files that have errors
python3 scripts/analyze_tdarr.py --requeue http://HOST:PORT

# Dump raw JSON for processed files
python3 scripts/analyze_tdarr.py --json http://HOST:PORT

# Delete files with transcode errors (prompts per file)
python3 scripts/analyze_tdarr.py --delete-errors http://HOST:PORT
```
