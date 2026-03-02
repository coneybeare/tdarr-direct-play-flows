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
