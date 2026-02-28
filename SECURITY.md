# Security Policy

## Reporting a Vulnerability

If you discover a security issue in this project, please open a [GitHub Security Advisory](https://github.com/coneybeare/tdarr-direct-play-flows/security/advisories/new) rather than a public issue.

## Scope

This project contains Tdarr flow JSON files — no executable code, no server, no authentication. The primary security concern is **accidental secret exposure**:

### Do not commit real credentials

The flow files contain placeholder values that must be replaced before use:

| Placeholder | Where | Replace with |
|---|---|---|
| `YOUR_PLEX_IP` | Notify Plex node | Your Plex server's local IP |
| `YOUR_PLEX_TOKEN` | Notify Plex node | Your Plex auth token |
| `YOUR_RADARR_IP` | Notify Arr node | Your Radarr server's local IP |
| `YOUR_RADARR_API_KEY` | Notify Arr node | Your Radarr API key |
| `YOUR_SONARR_*` | Notify Arr node | Your Sonarr equivalents |

**Configure these values directly in Tdarr's visual flow editor — not by editing the JSON files in this repository.**

If you accidentally commit a real token or API key:
1. Revoke it immediately in Plex / Radarr / Sonarr settings
2. Remove it from git history (`git filter-repo` or BFG)
3. Force-push the cleaned history
