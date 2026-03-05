# Claude Code Instructions

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

## PR Review Workflow

- After creating a PR, always check for and address Copilot/reviewer comments before considering work done.
- Use `gh api repos/{owner}/{repo}/pulls/{num}/comments` to fetch inline review comments.
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
