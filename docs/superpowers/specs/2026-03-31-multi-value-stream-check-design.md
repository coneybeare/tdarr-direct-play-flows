# Multi-Value Stream Property Check Plugin & Flow Simplification

## Problem

Files where the only audio is MP4-mux-incompatible (wmav2, adpcm_ima_wav, vorbis, opus) lose all audio during processing. `cmd_rmmux` strips them in pass 1 (required — FFmpeg crashes if they reach the MP4 muxer), but with no other audio present, EnsureAudioStream has no source material. The file completes encoding with no audio and hits `fail_no_streams`. Currently affects 8 files (7 wmav2, 1 adpcm).

Separately, the flow has two 11-node guard chains (main + VR) that each check audio `codec_name` one value at a time via `checkStreamProperty`. Tdarr's built-in plugin does not support comma-separated values, requiring one node per codec.

## Solution

Create a custom local Tdarr flow plugin (`checkStreamPropertyMultiValue`) that checks a stream property against a comma-separated list of values in a single node. Use it to:

1. Replace the main unwanted audio guard chain (11 nodes → 2)
2. Replace the VR unwanted audio guard chain (11 nodes → 2)
3. Add early detection for mux-incompatible-only audio (2 new nodes → route to manual review)

Net impact: 182 nodes → 166 nodes (-16).

## Plugin: checkStreamPropertyMultiValue

**Source:** `plugins/LocalFlowPlugins/checkStreamPropertyMultiValue/1.0.0/index.js`
**sourceRepo:** `"local"`

### Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `streamType` | dropdown: `all`, `video`, `audio`, `subtitle`, `data` | `all` | Which streams to inspect |
| `propertyToCheck` | text | `codec_name` | Dot-notation property path (e.g., `codec_name`, `tags.language`, `codec_tag_string`) |
| `valuesToMatch` | text | _(empty)_ | Comma-separated values to match against |
| `condition` | dropdown: `includes`, `equals` | `includes` | `includes` = substring match, `equals` = exact match |

### Behavior

1. Filter streams by `streamType` (skip if `all`).
2. For each matching stream, read the property via dot-notation, convert to lowercase string.
3. Split `valuesToMatch` on commas, trim whitespace, convert each to lowercase.
4. For each stream, check against every value:
   - `includes`: stream property string `.includes(value)`
   - `equals`: stream property string `=== value`
5. If ANY stream matches ANY value → output `1`.
6. If NO stream matches → output `2`.

### Output Handles

- `1` = at least one stream matched
- `2` = no streams matched

The flow wiring determines semantic meaning. For example, output `2` from an unwanted codec check means "no unwanted codecs found" (optimal). Output `2` from a safe audio check means "no safe audio found" (needs review).

## Flow Changes

### Use Case 1: Main Unwanted Audio Guard (11 → 2 nodes)

**Remove nodes:** `grd_unwanted_dts`, `grd_unwanted_dca`, `grd_unwanted_mp3`, `grd_unwanted_truehd`, `grd_unwanted_mlp`, `grd_unwanted_flac`, `grd_unwanted_vorbis`, `grd_unwanted_opus`, `grd_unwanted_pcm`, `grd_unwanted_wma`, `grd_unwanted_ac3`

**Remove edges:** All edges connecting the above nodes (11 YES→cmt_proc edges, 10 NO→next-guard edges, 1 incoming from grd_has_eac3, 1 outgoing to fl_noop).

**Add nodes:**

| ID | pluginName | condition | valuesToMatch |
|----|-----------|-----------|---------------|
| `grd_unwanted_exact` | checkStreamPropertyMultiValue | `equals` | `dts,dca,mp3,truehd,mlp,flac,vorbis,opus,ac3` |
| `grd_unwanted_partial` | checkStreamPropertyMultiValue | `includes` | `pcm,wma` |

Both: `streamType: "audio"`, `propertyToCheck: "codec_name"`, `sourceRepo: "local"`.

**Add edges:**
```
grd_has_eac3 NO(2) → grd_unwanted_exact
grd_unwanted_exact YES(1) → cmt_proc
grd_unwanted_exact NO(2) → grd_unwanted_partial
grd_unwanted_partial YES(1) → cmt_proc
grd_unwanted_partial NO(2) → fl_noop
```

**Why two nodes:** `ac3` must use `equals` to avoid matching `eac3` (which is wanted). `pcm` and `wma` must use `includes` to catch variant families (pcm_s16le, pcm_s24le, wmav2, wmapro). These require different conditions, hence two nodes.

### Use Case 2: VR Unwanted Audio Guard (11 → 2 nodes)

**Remove nodes:** `grd_vr_nw_dts`, `grd_vr_nw_dca`, `grd_vr_nw_mp3`, `grd_vr_nw_truehd`, `grd_vr_nw_mlp`, `grd_vr_nw_flac`, `grd_vr_nw_vorbis`, `grd_vr_nw_opus`, `grd_vr_nw_pcm`, `grd_vr_nw_wma`, `grd_vr_nowanted_ac3`

**Remove edges:** All edges connecting the above nodes.

**Add nodes:**

| ID | pluginName | condition | valuesToMatch |
|----|-----------|-----------|---------------|
| `grd_vr_unwanted_exact` | checkStreamPropertyMultiValue | `equals` | `dts,dca,mp3,truehd,mlp,flac,vorbis,opus,ac3` |
| `grd_vr_unwanted_partial` | checkStreamPropertyMultiValue | `includes` | `pcm,wma` |

Both: `streamType: "audio"`, `propertyToCheck: "codec_name"`, `sourceRepo: "local"`.

**Add edges:**
```
grd_vr_ishevc YES(1) → grd_vr_unwanted_exact
grd_vr_unwanted_exact YES(1) → cmt_vr
grd_vr_unwanted_exact NO(2) → grd_vr_unwanted_partial
grd_vr_unwanted_partial YES(1) → cmt_vr
grd_vr_unwanted_partial NO(2) → grd_vr_hasaac
```

### Use Case 3: Mux-Incompatible-Only Detection (2 new nodes)

Inserted after VR detection NO path, before pass 1 FFmpeg start.

**Add nodes:**

| ID | pluginName | condition | valuesToMatch |
|----|-----------|-----------|---------------|
| `grd_has_muxincompat` | checkStreamPropertyMultiValue | `includes` | `wma,adpcm,vorbis,opus` |
| `grd_has_safe_audio` | checkStreamPropertyMultiValue | `equals` | `aac,ac3,eac3,mp3,dts,dca,truehd,mlp,flac,pcm_s16le,pcm_s24le` |

Both: `streamType: "audio"`, `propertyToCheck: "codec_name"`, `sourceRepo: "local"`.

**Add edges:**
```
[VR detection NO] → grd_has_muxincompat
grd_has_muxincompat NO(2) → [pass 1 start, as before]
grd_has_muxincompat YES(1) → grd_has_safe_audio
grd_has_safe_audio YES(1) → [pass 1 start, as before]
grd_has_safe_audio NO(2) → fl_manual_review
```

**Why `equals` for safe audio:** Uses exact matching with explicit PCM variants (`pcm_s16le`, `pcm_s24le`) instead of substring `includes` with `pcm` — `adpcm_ima_wav` contains "pcm" as a substring, which would cause a false positive. If an unlisted exotic codec is the only safe audio, the file goes to manual review — safe false positive.

**Not added to VR pipeline:** No VR files have hit this issue. Can be added later if needed.

## Testing

### Plugin Unit Tests

New file: `tests/check-stream-multi-value.test.mjs`

- `equals` condition: exact match against comma-separated values, case-insensitive
- `includes` condition: substring match, catches variant families (pcm_s24le matches "pcm")
- `equals` does NOT substring match (`eac3` does not match value `ac3`)
- Stream type filtering: only inspects streams matching `streamType`
- Dot-notation property access (e.g., `tags.language`)
- Edge cases: no audio streams returns output `2`, empty valuesToMatch returns output `2`
- Multiple streams: returns `1` if ANY stream matches (not all)

### Permutation Matrix Updates

- Add `checkStreamPropertyMultiValue` handler to the walkFlow simulator with same semantics as plugin
- Replace old 11-node guard references with 2-node equivalents
- Add test cases:
  - `wmv/wmv2/wmav2 2ch` → routes to `fl_manual_review` (mux-incompatible only)
  - `mkv/hevc/wmav2 2ch + ac3 5.1` → processes normally (has safe audio)
  - `mkv/hevc/adpcm 2ch` → routes to `fl_manual_review`
- Existing test scenarios must produce identical routing results

### Flow Validation Updates

- Remove assertions for 22 deleted guard nodes
- Add assertions for 6 new nodes: correct pluginName, sourceRepo "local", edge wiring
- Verify `sourceRepo: "local"` passes validation (already supported)

## Deployment

### Plugin Files

Plugin source lives in the repo at `plugins/LocalFlowPlugins/checkStreamPropertyMultiValue/1.0.0/index.js`. This mirrors Tdarr's expected directory structure.

### Server Deployment

Extend `deploy_flow.py` to sync `plugins/LocalFlowPlugins/` to each server's Tdarr plugin directory before deploying the flow JSON. The server plugin path is configured in `servers.local.json` (new field: `plugin_path`). Plugin deployment runs first so the plugin is available when the updated flow references it.

### Flow JSON

New nodes use `"sourceRepo": "local"` instead of `"Community"`.

## Summary

| Change | Before | After |
|--------|--------|-------|
| Main unwanted guard | 11 nodes | 2 nodes |
| VR unwanted guard | 11 nodes | 2 nodes |
| Mux-incompatible detection | 0 nodes (fail_no_streams) | 2 nodes (manual review) |
| Total flow nodes | 182 | 166 |
| Plugin files | 0 | 1 |
