# Multi-Value Stream Property Check Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a custom Tdarr flow plugin for multi-value stream property checks, use it to collapse 22 guard nodes into 4 and add early mux-incompatible audio detection routing to manual review.

**Architecture:** A local Tdarr flow plugin (`checkStreamPropertyMultiValue`) that splits comma-separated values and checks stream properties against each. The flow JSON replaces two 11-node guard chains with 2-node equivalents and adds a 2-node mux-incompatible-only detection gate before pass 1.

**Tech Stack:** Node.js (Tdarr plugin), JSON (flow definition), Python (layout script), JavaScript (test files)

**Spec:** `docs/superpowers/specs/2026-03-31-multi-value-stream-check-design.md`

---

### Task 1: Create plugin with unit tests (TDD)

**Files:**
- Create: `plugins/LocalFlowPlugins/checkStreamPropertyMultiValue/1.0.0/index.js`
- Create: `tests/check-stream-multi-value.test.mjs`

- [ ] **Step 1: Write failing unit tests**

Create `tests/check-stream-multi-value.test.mjs`:

```javascript
/**
 * Unit tests for the checkStreamPropertyMultiValue local flow plugin.
 *
 * Tests the plugin function directly with mock args objects,
 * verifying multi-value matching with both "equals" and "includes" conditions.
 */

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Import the plugin module
const pluginPath = join(__dirname, '..', 'plugins', 'LocalFlowPlugins',
  'checkStreamPropertyMultiValue', '1.0.0', 'index.js');
const { plugin, details } = await import(pluginPath);

// ── Helper: build mock args ──────────────────────────────────────
function mockArgs(streams, inputsDB) {
  return {
    inputFileObj: {
      ffProbeData: { streams },
    },
    inputs: inputsDB,
    variables: {},
  };
}

function audioStream(codec_name, opts = {}) {
  return {
    codec_type: 'audio',
    codec_name,
    channels: opts.channels || 2,
    tags: opts.tags || {},
  };
}

function videoStream(codec_name, opts = {}) {
  return {
    codec_type: 'video',
    codec_name,
    codec_tag_string: opts.tag || '',
    height: opts.height || 1080,
    tags: opts.tags || {},
  };
}

// ── Tests ────────────────────────────────────────────────────────
describe('checkStreamPropertyMultiValue plugin', () => {
  test('details() returns valid metadata', () => {
    const d = details();
    assert.ok(d.name, 'Must have a name');
    assert.ok(d.Inputs.length === 4, 'Must have 4 inputs');
  });

  describe('equals condition', () => {
    test('matches exact codec_name from comma list', () => {
      const args = mockArgs(
        [videoStream('hevc'), audioStream('dts')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'dts,dca,mp3,ac3', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });

    test('no match when codec not in list', () => {
      const args = mockArgs(
        [videoStream('hevc'), audioStream('aac')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'dts,dca,mp3,ac3', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2');
    });

    test('eac3 does NOT match ac3 with equals', () => {
      const args = mockArgs(
        [audioStream('eac3')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'ac3', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2',
        'equals must not substring match — eac3 must NOT match ac3');
    });

    test('case insensitive matching', () => {
      const args = mockArgs(
        [audioStream('DTS')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'dts', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });

    test('trims whitespace around commas', () => {
      const args = mockArgs(
        [audioStream('mp3')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'dts , mp3 , ac3', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });
  });

  describe('includes condition', () => {
    test('substring matches pcm variants', () => {
      const args = mockArgs(
        [audioStream('pcm_s24le')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'pcm,wma', condition: 'includes' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });

    test('substring matches wma variants', () => {
      const args = mockArgs(
        [audioStream('wmav2')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'pcm,wma', condition: 'includes' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });

    test('no match when no substring matches', () => {
      const args = mockArgs(
        [audioStream('aac')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'pcm,wma', condition: 'includes' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2');
    });

    test('ac3 includes matches eac3 (expected behavior)', () => {
      const args = mockArgs(
        [audioStream('eac3')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'ac3', condition: 'includes' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1',
        'includes should substring match — eac3 contains ac3');
    });
  });

  describe('stream type filtering', () => {
    test('only checks audio streams when streamType is audio', () => {
      const args = mockArgs(
        [videoStream('hevc', { tag: 'hvc1' }), audioStream('aac')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'hevc', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2',
        'Should not match video codec when streamType is audio');
    });

    test('checks all streams when streamType is all', () => {
      const args = mockArgs(
        [videoStream('hevc'), audioStream('aac')],
        { streamType: 'all', propertyToCheck: 'codec_name',
          valuesToMatch: 'hevc', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });
  });

  describe('dot-notation property access', () => {
    test('reads nested tags.language', () => {
      const args = mockArgs(
        [audioStream('aac', { tags: { language: 'eng' } })],
        { streamType: 'audio', propertyToCheck: 'tags.language',
          valuesToMatch: 'eng', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });

    test('returns 2 when nested property missing', () => {
      const args = mockArgs(
        [audioStream('aac')],
        { streamType: 'audio', propertyToCheck: 'tags.language',
          valuesToMatch: 'eng', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2');
    });
  });

  describe('edge cases', () => {
    test('no audio streams returns 2', () => {
      const args = mockArgs(
        [videoStream('hevc')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'aac', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2');
    });

    test('empty valuesToMatch returns 2', () => {
      const args = mockArgs(
        [audioStream('aac')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: '', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '2');
    });

    test('multiple audio streams — returns 1 if ANY matches', () => {
      const args = mockArgs(
        [audioStream('aac'), audioStream('ac3'), audioStream('dts')],
        { streamType: 'audio', propertyToCheck: 'codec_name',
          valuesToMatch: 'dts', condition: 'equals' },
      );
      const result = plugin(args);
      assert.strictEqual(result.outputNumber, '1');
    });
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `node --test tests/check-stream-multi-value.test.mjs`
Expected: FAIL — cannot find module (plugin doesn't exist yet)

- [ ] **Step 3: Create the plugin**

Create `plugins/LocalFlowPlugins/checkStreamPropertyMultiValue/1.0.0/index.js`:

```javascript
/* eslint-disable no-param-reassign */

function getNestedProp(obj, path) {
  return path.split('.').reduce((o, k) => (o != null ? o[k] : undefined), obj);
}

function details() {
  return {
    name: 'Check Stream Property (Multi-Value)',
    Operation: 'Filter',
    Description: 'Check if any stream has a property matching any of the comma-separated values.',
    Version: '1.0.0',
    Tags: 'audio,video,filter',
    Inputs: [
      {
        name: 'streamType',
        type: 'string',
        defaultValue: 'all',
        inputUI: { type: 'dropdown', options: ['all', 'video', 'audio', 'subtitle', 'data'] },
        tooltip: 'Which stream type to check',
      },
      {
        name: 'propertyToCheck',
        type: 'string',
        defaultValue: 'codec_name',
        inputUI: { type: 'text' },
        tooltip: 'Dot-notation property path (e.g. codec_name, tags.language)',
      },
      {
        name: 'valuesToMatch',
        type: 'string',
        defaultValue: '',
        inputUI: { type: 'text' },
        tooltip: 'Comma-separated values to check against',
      },
      {
        name: 'condition',
        type: 'string',
        defaultValue: 'includes',
        inputUI: { type: 'dropdown', options: ['includes', 'equals'] },
        tooltip: 'includes = substring match, equals = exact match',
      },
    ],
  };
}

function plugin(args) {
  const { streamType, propertyToCheck, valuesToMatch, condition } = args.inputs;
  const streams = (args.inputFileObj.ffProbeData || {}).streams || [];

  const filtered = streamType === 'all'
    ? streams
    : streams.filter((s) => s.codec_type === streamType);

  const values = (valuesToMatch || '')
    .split(',')
    .map((v) => v.trim().toLowerCase())
    .filter((v) => v.length > 0);

  if (values.length === 0) {
    return { outputFileObj: args.inputFileObj, outputNumber: '2', variables: args.variables };
  }

  for (const stream of filtered) {
    const prop = getNestedProp(stream, propertyToCheck);
    if (prop == null) continue;
    const propStr = String(prop).toLowerCase();

    for (const val of values) {
      if (condition === 'equals' && propStr === val) {
        return { outputFileObj: args.inputFileObj, outputNumber: '1', variables: args.variables };
      }
      if (condition === 'includes' && propStr.includes(val)) {
        return { outputFileObj: args.inputFileObj, outputNumber: '1', variables: args.variables };
      }
    }
  }

  return { outputFileObj: args.inputFileObj, outputNumber: '2', variables: args.variables };
}

module.exports = { details, plugin };
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `node --test tests/check-stream-multi-value.test.mjs`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/ tests/check-stream-multi-value.test.mjs
git commit -m "feat: add checkStreamPropertyMultiValue local plugin with tests"
```

---

### Task 2: Update flow JSON — replace guard chains and add mux-incompatible detection

**Files:**
- Modify: `flows/01_hevc_mp4_direct_play.json`

**Important context:** All `inputsDB` values must be strings. `sourceRepo` must be `"local"` for the new plugin. Every node needs `fpEnabled: true`. Position values are placeholders — `layout_flows.py` will recompute them in Task 5.

- [ ] **Step 1: Remove 22 old guard nodes from flowPlugins**

Remove all plugin entries with these IDs from the `flowPlugins` array:

**Main chain (11):** `grd_unwanted_dts`, `grd_unwanted_dca`, `grd_unwanted_mp3`, `grd_unwanted_truehd`, `grd_unwanted_mlp`, `grd_unwanted_flac`, `grd_unwanted_vorbis`, `grd_unwanted_opus`, `grd_unwanted_pcm`, `grd_unwanted_wma`, `grd_unwanted_ac3`

**VR chain (11):** `grd_vr_nw_dts`, `grd_vr_nw_dca`, `grd_vr_nw_mp3`, `grd_vr_nw_truehd`, `grd_vr_nw_mlp`, `grd_vr_nw_flac`, `grd_vr_nw_vorbis`, `grd_vr_nw_opus`, `grd_vr_nw_pcm`, `grd_vr_nw_wma`, `grd_vr_nowanted_ac3`

- [ ] **Step 2: Remove all edges referencing removed nodes**

Remove every edge from `flowEdges` where `source` or `target` is one of the 22 removed node IDs. This includes:
- 11 YES edges (guard → `cmt_proc`) for main chain
- 10 NO edges (guard → next guard) for main chain
- 1 incoming edge (`grd_has_eac3:2` → `grd_unwanted_dts`)
- 1 incoming edge (`grd_surr_ch:1` → `grd_unwanted_dts`)
- 1 outgoing edge (`grd_unwanted_ac3:2` → `cmt_optimal`)
- 11 YES edges (guard → `cmt_vr`) for VR chain
- 10 NO edges (guard → next guard) for VR chain
- 1 incoming edge (`grd_vr_ishevc:1` → `grd_vr_nw_dts`)
- 1 outgoing edge (`grd_vr_nowanted_ac3:2` → `grd_vr_hasaac`)

- [ ] **Step 3: Add 6 new plugin nodes**

Add to `flowPlugins`:

```json
{
  "name": "Unwanted audio? (exact)",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_unwanted_exact",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "dts,dca,mp3,truehd,mlp,flac,vorbis,opus,ac3",
    "condition": "equals"
  },
  "fpEnabled": true
},
{
  "name": "Unwanted audio? (partial)",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_unwanted_partial",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "pcm,wma",
    "condition": "includes"
  },
  "fpEnabled": true
},
{
  "name": "VR unwanted? (exact)",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_vr_unwanted_exact",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "dts,dca,mp3,truehd,mlp,flac,vorbis,opus,ac3",
    "condition": "equals"
  },
  "fpEnabled": true
},
{
  "name": "VR unwanted? (partial)",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_vr_unwanted_partial",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "pcm,wma",
    "condition": "includes"
  },
  "fpEnabled": true
},
{
  "name": "Mux-incompatible audio?",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_has_muxincompat",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "wma,adpcm,vorbis,opus",
    "condition": "includes"
  },
  "fpEnabled": true
},
{
  "name": "Has safe audio?",
  "sourceRepo": "local",
  "pluginName": "checkStreamPropertyMultiValue",
  "version": "1.0.0",
  "id": "grd_has_safe_audio",
  "position": {"x": 0, "y": 0},
  "inputsDB": {
    "streamType": "audio",
    "propertyToCheck": "codec_name",
    "valuesToMatch": "aac,ac3,eac3,mp3,dts,dca,truehd,mlp,flac,pcm",
    "condition": "includes"
  },
  "fpEnabled": true
}
```

- [ ] **Step 4: Add new edges for main unwanted guard (Use Case 1)**

Current wiring to replace:
- `grd_surr_ch:1` → `grd_unwanted_dts` (was start of chain)
- `grd_has_eac3:2` → `grd_unwanted_dts` (was alternate entry)
- `grd_unwanted_ac3:2` → `cmt_optimal` (was end of chain)

Add these edges (use descriptive IDs):

```json
{"source": "grd_surr_ch", "sourceHandle": "1", "target": "grd_unwanted_exact", "targetHandle": null, "id": "e_surr_to_unwanted_exact"},
{"source": "grd_has_eac3", "sourceHandle": "2", "target": "grd_unwanted_exact", "targetHandle": null, "id": "e_eac3_to_unwanted_exact"},
{"source": "grd_unwanted_exact", "sourceHandle": "1", "target": "cmt_proc", "targetHandle": null, "id": "e_unwanted_exact_yes"},
{"source": "grd_unwanted_exact", "sourceHandle": "2", "target": "grd_unwanted_partial", "targetHandle": null, "id": "e_unwanted_exact_no"},
{"source": "grd_unwanted_partial", "sourceHandle": "1", "target": "cmt_proc", "targetHandle": null, "id": "e_unwanted_partial_yes"},
{"source": "grd_unwanted_partial", "sourceHandle": "2", "target": "cmt_optimal", "targetHandle": null, "id": "e_unwanted_partial_no"}
```

- [ ] **Step 5: Add new edges for VR unwanted guard (Use Case 2)**

Current wiring to replace:
- `grd_vr_ishevc:1` → `grd_vr_nw_dts` (was start of chain)
- `grd_vr_nowanted_ac3:2` → `grd_vr_hasaac` (was end of chain)

Add these edges:

```json
{"source": "grd_vr_ishevc", "sourceHandle": "1", "target": "grd_vr_unwanted_exact", "targetHandle": null, "id": "e_vr_hevc_to_unwanted_exact"},
{"source": "grd_vr_unwanted_exact", "sourceHandle": "1", "target": "cmt_vr", "targetHandle": null, "id": "e_vr_unwanted_exact_yes"},
{"source": "grd_vr_unwanted_exact", "sourceHandle": "2", "target": "grd_vr_unwanted_partial", "targetHandle": null, "id": "e_vr_unwanted_exact_no"},
{"source": "grd_vr_unwanted_partial", "sourceHandle": "1", "target": "cmt_vr", "targetHandle": null, "id": "e_vr_unwanted_partial_yes"},
{"source": "grd_vr_unwanted_partial", "sourceHandle": "2", "target": "grd_vr_hasaac", "targetHandle": null, "id": "e_vr_unwanted_partial_no"}
```

- [ ] **Step 6: Add new edges for mux-incompatible detection (Use Case 3)**

Current wiring to replace:
- `chk_vr:2` → `ffs_001` (edge ID: `e_vr_no`)

Remove the `e_vr_no` edge. Add:

```json
{"source": "chk_vr", "sourceHandle": "2", "target": "grd_has_muxincompat", "targetHandle": null, "id": "e_vr_no_muxcheck"},
{"source": "grd_has_muxincompat", "sourceHandle": "2", "target": "ffs_001", "targetHandle": null, "id": "e_muxcheck_safe"},
{"source": "grd_has_muxincompat", "sourceHandle": "1", "target": "grd_has_safe_audio", "targetHandle": null, "id": "e_muxcheck_has_incompat"},
{"source": "grd_has_safe_audio", "sourceHandle": "1", "target": "ffs_001", "targetHandle": null, "id": "e_safeaudio_yes"},
{"source": "grd_has_safe_audio", "sourceHandle": "2", "target": "fl_manual_review", "targetHandle": null, "id": "e_safeaudio_no_review"}
```

- [ ] **Step 7: Verify JSON is valid**

Run: `node -e "JSON.parse(require('fs').readFileSync('flows/01_hevc_mp4_direct_play.json','utf8')); console.log('Valid JSON')"`
Expected: `Valid JSON`

- [ ] **Step 8: Commit**

```bash
git add flows/01_hevc_mp4_direct_play.json
git commit -m "refactor: replace 22 guard nodes with 6 multi-value plugin nodes

Use Cases:
- Main unwanted audio guard: 11 nodes → 2
- VR unwanted audio guard: 11 nodes → 2
- Mux-incompatible-only detection: routes to manual review (2 new nodes)

Flow: 182 → 166 nodes"
```

---

### Task 3: Update permutation matrix simulator and tests

**Files:**
- Modify: `tests/permutation-matrix.test.mjs`

**Context:** The `evaluateNode` function (around line 42) has a `switch` on `pluginName`. Add a case for `checkStreamPropertyMultiValue`. The `lacks` helper already exists (line 197). The `has` helper is at line 193.

- [ ] **Step 1: Add checkStreamPropertyMultiValue handler to evaluateNode**

In `tests/permutation-matrix.test.mjs`, add a new case to the `switch (node.pluginName)` block, after the existing `checkStreamProperty` case (around line 75):

```javascript
    case 'checkStreamPropertyMultiValue': {
      const streams = file.streams.filter(
        (s) => db.streamType === 'all' || s.codec_type === db.streamType
      );
      const values = (db.valuesToMatch || '').split(',').map((v) => v.trim().toLowerCase()).filter((v) => v.length > 0);
      if (values.length === 0) return '2';
      for (const stream of streams) {
        const prop = getNestedProp(stream, db.propertyToCheck);
        if (prop == null) continue;
        const propStr = String(prop).toLowerCase();
        for (const val of values) {
          if (db.condition === 'equals' && propStr === val) return '1';
          if (db.condition === 'includes' && propStr.includes(val)) return '1';
        }
      }
      return '2';
    }
```

- [ ] **Step 2: Update guard chain references in existing tests**

Replace references to the old guard chain nodes in test assertions. Specifically:

In tests that check `has(path, 'grd_unwanted_dts', ...)`, replace with `has(path, 'grd_unwanted_exact', ...)` or `has(path, 'grd_unwanted_partial', ...)` as appropriate. The key change: any test that previously asserted individual guard nodes now asserts the collapsed equivalents.

Search for all references to the old guard IDs (`grd_unwanted_dts`, `grd_unwanted_dca`, etc.) and update or remove them. Most permutation tests only use `assertProcess(path)` which checks for `ffs_001` — those don't need changes since the routing outcome is the same.

If any test has `has(path, 'grd_unwanted_dts')`, change to `has(path, 'grd_unwanted_exact')`.
If any test has `has(path, 'grd_vr_nw_dts')`, change to `has(path, 'grd_vr_unwanted_exact')`.

- [ ] **Step 3: Add mux-incompatible-only test cases**

Add a new describe block in the permutation matrix after the existing "Edge cases" section (before "Guard chain detail"):

```javascript
  describe('11. Mux-incompatible audio detection', () => {
    test('11a: wmv/wmv2/wmav2 2ch — routes to manual review (only mux-incompatible audio)', () => {
      const path = walkFlow(file('wmv', [vid('wmv2', { tag: '' }), aud('wmav2', 2, '')]));
      has(path, 'grd_has_muxincompat', 'Should check for mux-incompatible audio');
      has(path, 'grd_has_safe_audio', 'Should check for safe audio');
      has(path, 'fl_manual_review', 'Should route to manual review');
      lacks(path, 'ffs_001', 'Should NOT enter encoding pipeline');
    });

    test('11b: mkv/hevc/adpcm_ima_wav 2ch — routes to manual review', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('adpcm_ima_wav', 2, '')]));
      has(path, 'grd_has_muxincompat', 'Should detect adpcm');
      has(path, 'fl_manual_review', 'Should route to manual review');
      lacks(path, 'ffs_001', 'Should NOT enter encoding pipeline');
    });

    test('11c: mkv/hevc/wmav2 2ch + ac3 5.1 — processes normally (has safe audio)', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('wmav2', 2, 'hun'), aud('ac3', 6, 'eng')]));
      has(path, 'grd_has_muxincompat', 'Should detect mux-incompatible');
      has(path, 'grd_has_safe_audio', 'Should find safe audio');
      has(path, 'ffs_001', 'Should enter encoding pipeline');
      lacks(path, 'fl_manual_review', 'Should NOT route to manual review');
    });

    test('11d: mkv/hevc/ac3 5.1 — no mux-incompatible, proceeds normally', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6, 'eng')]));
      has(path, 'grd_has_muxincompat', 'Should check mux-incompatible');
      lacks(path, 'grd_has_safe_audio', 'Should skip safe audio check (no mux-incompatible found)');
      has(path, 'ffs_001', 'Should enter encoding pipeline');
    });
  });
```

- [ ] **Step 4: Update guard chain detail tests**

In the "Guard chain detail" describe block, update any tests that assert specific old guard node IDs. The first test (`mp4/hevc(hvc1)/aac 2.0 + no surround + no unwanted → optimal`) should now check:

```javascript
    test('mp4/hevc(hvc1)/aac 2.0 + no surround + no unwanted → optimal', () => {
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 2)]));
      // Full guard chain traversal
      has(path, 'grd_ext');
      has(path, 'grd_vid');
      has(path, 'grd_dovi');
      has(path, 'grd_tag');
      has(path, 'grd_aud');
      has(path, 'grd_ch');
      has(path, 'grd_surr_ch');
      has(path, 'grd_has_eac3');
      has(path, 'grd_unwanted_exact');
      has(path, 'grd_unwanted_partial');
      has(path, 'fl_noop');
    });
```

- [ ] **Step 5: Run tests**

Run: `npm test`
Expected: All tests PASS (plugin unit tests + permutation matrix + validate-flows).
Note: validate-flows will likely FAIL at this point because it still has old guard chain assertions. That's OK — Task 4 fixes it.

- [ ] **Step 6: Commit**

```bash
git add tests/permutation-matrix.test.mjs
git commit -m "test: update permutation matrix for multi-value guard collapse"
```

---

### Task 4: Update validate-flows tests

**Files:**
- Modify: `tests/validate-flows.test.mjs`

- [ ] **Step 1: Replace the unwanted audio guard chain test**

Replace the entire `'guard chain catches unwanted audio codecs (individual single-value guards)'` test (around line 465–541) with:

```javascript
    test('guard chain catches unwanted audio codecs (multi-value plugin)', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Main unwanted guard: 2 multi-value nodes replace 11 single-value guards
      // Exact match node: dts,dca,mp3,truehd,mlp,flac,vorbis,opus,ac3
      const exact = pluginMap.get('grd_unwanted_exact');
      assert.ok(exact, 'Missing node grd_unwanted_exact');
      assert.strictEqual(exact.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(exact.sourceRepo, 'local');
      assert.strictEqual(exact.inputsDB.streamType, 'audio');
      assert.strictEqual(exact.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(exact.inputsDB.condition, 'equals');
      for (const codec of ['dts', 'dca', 'mp3', 'truehd', 'mlp', 'flac', 'vorbis', 'opus', 'ac3']) {
        assert.ok(exact.inputsDB.valuesToMatch.split(',').map(s => s.trim()).includes(codec),
          `grd_unwanted_exact must include ${codec}`);
      }

      // Partial match node: pcm,wma
      const partial = pluginMap.get('grd_unwanted_partial');
      assert.ok(partial, 'Missing node grd_unwanted_partial');
      assert.strictEqual(partial.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(partial.sourceRepo, 'local');
      assert.strictEqual(partial.inputsDB.condition, 'includes');
      for (const codec of ['pcm', 'wma']) {
        assert.ok(partial.inputsDB.valuesToMatch.split(',').map(s => s.trim()).includes(codec),
          `grd_unwanted_partial must include ${codec}`);
      }

      // Wiring: both entry points → grd_unwanted_exact
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted_exact',
        'Surround files should route to unwanted exact check');
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted_exact',
        'No EAC3 should route to unwanted exact check');

      // grd_unwanted_exact YES → cmt_proc, NO → grd_unwanted_partial
      assert.strictEqual(edgeMap.get('grd_unwanted_exact:1'), 'cmt_proc');
      assert.strictEqual(edgeMap.get('grd_unwanted_exact:2'), 'grd_unwanted_partial');

      // grd_unwanted_partial YES → cmt_proc, NO → cmt_optimal
      assert.strictEqual(edgeMap.get('grd_unwanted_partial:1'), 'cmt_proc');
      assert.strictEqual(edgeMap.get('grd_unwanted_partial:2'), 'cmt_optimal');

      // Old single-value guards must be gone
      const oldGuards = [
        'grd_unwanted_dts', 'grd_unwanted_dca', 'grd_unwanted_mp3',
        'grd_unwanted_truehd', 'grd_unwanted_mlp', 'grd_unwanted_flac',
        'grd_unwanted_vorbis', 'grd_unwanted_opus', 'grd_unwanted_pcm',
        'grd_unwanted_wma', 'grd_unwanted_ac3',
      ];
      for (const id of oldGuards) {
        assert.ok(!pluginMap.has(id), `${id} must be removed (replaced by multi-value plugin)`);
      }

      // Every guarded codec must still be removable by the pipeline
      const rmaudio = pluginMap.get('cmd_rmaudio');
      const rmmux = pluginMap.get('cmd_rmmux');
      const rmAc3 = pluginMap.get('cmd_rm_ac3');
      const rmMp3 = pluginMap.get('cmd_rm_mp3');
      assert.ok(rmaudio && rmmux && rmAc3 && rmMp3, 'Missing removal nodes');
      const tagToCodec = { 'ac-3': 'ac3' };
      const rmAc3Codecs = rmAc3.inputsDB.propertyToCheck === 'codec_tag_string'
        ? rmAc3.inputsDB.valuesToRemove.split(',').map(s => tagToCodec[s.trim()] || s.trim())
        : rmAc3.inputsDB.valuesToRemove.split(',').map(s => s.trim());
      const removableSet = new Set([
        ...rmaudio.inputsDB.valuesToRemove.split(',').map(s => s.trim()),
        ...rmmux.inputsDB.valuesToRemove.split(',').map(s => s.trim()),
        ...rmAc3Codecs,
        rmMp3.inputsDB.valuesToRemove.trim(),
      ]);
      const allGuarded = [
        ...exact.inputsDB.valuesToMatch.split(',').map(s => s.trim()),
        ...partial.inputsDB.valuesToMatch.split(',').map(s => s.trim()),
      ];
      for (const codec of allGuarded) {
        // "pcm" and "wma" are substring patterns — check if any removable contains them
        if (['pcm', 'wma'].includes(codec)) {
          const hasMatch = [...removableSet].some(r => r.includes(codec));
          assert.ok(hasMatch,
            `Guard catches "${codec}" (includes) but no removal node strips a matching codec`);
        } else {
          assert.ok(removableSet.has(codec),
            `Guard catches "${codec}" but no removal node strips it`);
        }
      }
    });
```

- [ ] **Step 2: Update VR retag shortcut guards test**

In the `'VR retag shortcut guards and pipeline wiring'` test (around line 766), replace the VR guard chain section:

Replace this section (lines ~777–797):
```javascript
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:1'), 'grd_vr_nw_dts',
        'HEVC VR should start unwanted audio chain');
      ...
      // VR unwanted audio guard chain (individual single-value guards)
      const vrGuardChain = [ ... ];
      for (let i = 0; ...) { ... }
```

With:
```javascript
      // VR unwanted guard: 2 multi-value nodes
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:1'), 'grd_vr_unwanted_exact',
        'HEVC VR should start multi-value unwanted check');
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:2'), 'cmt_vr');

      const vrExact = pluginMap.get('grd_vr_unwanted_exact');
      assert.ok(vrExact, 'Missing node grd_vr_unwanted_exact');
      assert.strictEqual(vrExact.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(vrExact.sourceRepo, 'local');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_exact:1'), 'cmt_vr');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_exact:2'), 'grd_vr_unwanted_partial');

      const vrPartial = pluginMap.get('grd_vr_unwanted_partial');
      assert.ok(vrPartial, 'Missing node grd_vr_unwanted_partial');
      assert.strictEqual(vrPartial.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(vrPartial.sourceRepo, 'local');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_partial:1'), 'cmt_vr');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_partial:2'), 'grd_vr_hasaac');

      // Old VR guards must be gone
      const oldVrGuards = [
        'grd_vr_nw_dts', 'grd_vr_nw_dca', 'grd_vr_nw_mp3',
        'grd_vr_nw_truehd', 'grd_vr_nw_mlp', 'grd_vr_nw_flac',
        'grd_vr_nw_vorbis', 'grd_vr_nw_opus', 'grd_vr_nw_pcm',
        'grd_vr_nw_wma', 'grd_vr_nowanted_ac3',
      ];
      for (const id of oldVrGuards) {
        assert.ok(!pluginMap.has(id), `${id} must be removed`);
      }
```

- [ ] **Step 3: Add mux-incompatible detection test**

Add a new test in the validate-flows describe block:

```javascript
    test('mux-incompatible-only audio routes to manual review', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // chk_vr NO → grd_has_muxincompat (not directly to ffs_001)
      assert.strictEqual(edgeMap.get('chk_vr:2'), 'grd_has_muxincompat',
        'VR NO should route to mux-incompatible check');

      // grd_has_muxincompat config
      const muxCheck = pluginMap.get('grd_has_muxincompat');
      assert.ok(muxCheck, 'Missing node grd_has_muxincompat');
      assert.strictEqual(muxCheck.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(muxCheck.sourceRepo, 'local');
      assert.strictEqual(muxCheck.inputsDB.condition, 'includes');
      for (const codec of ['wma', 'adpcm', 'vorbis', 'opus']) {
        assert.ok(muxCheck.inputsDB.valuesToMatch.includes(codec),
          `grd_has_muxincompat must check for ${codec}`);
      }

      // NO mux-incompatible → ffs_001 (normal path)
      assert.strictEqual(edgeMap.get('grd_has_muxincompat:2'), 'ffs_001',
        'No mux-incompatible audio should proceed to pass 1');

      // YES mux-incompatible → check for safe audio
      assert.strictEqual(edgeMap.get('grd_has_muxincompat:1'), 'grd_has_safe_audio',
        'Mux-incompatible found should check for safe audio');

      // grd_has_safe_audio config
      const safeCheck = pluginMap.get('grd_has_safe_audio');
      assert.ok(safeCheck, 'Missing node grd_has_safe_audio');
      assert.strictEqual(safeCheck.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(safeCheck.inputsDB.condition, 'includes');

      // YES safe audio → ffs_001
      assert.strictEqual(edgeMap.get('grd_has_safe_audio:1'), 'ffs_001',
        'Has safe audio should proceed to pass 1');

      // NO safe audio → manual review
      assert.strictEqual(edgeMap.get('grd_has_safe_audio:2'), 'fl_manual_review',
        'No safe audio (only mux-incompatible) should route to manual review');
    });
```

- [ ] **Step 4: Update orphaned EAC3 test references**

In the `'guard chain catches orphaned stereo EAC3'` test, update references from `grd_unwanted_dts` to `grd_unwanted_exact`:

```javascript
      // grd_surr_ch YES → grd_unwanted_exact (has surround, start unwanted audio check)
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted_exact',
        'Files with surround should start unwanted audio check');
      ...
      // grd_has_eac3 NO → grd_unwanted_exact (no EAC3, start unwanted audio check)
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted_exact',
        'No EAC3 should start unwanted audio check');
```

- [ ] **Step 5: Run full test suite**

Run: `npm test`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add tests/validate-flows.test.mjs
git commit -m "test: update validation tests for multi-value guard collapse and mux detection"
```

---

### Task 5: Update layout script and regenerate

**Files:**
- Modify: `scripts/layout_flows.py`
- Modified by script: `flows/01_hevc_mp4_direct_play.json` (positions)
- Modified by script: `images/01_hevc_mp4_direct_play.svg`

- [ ] **Step 1: Update col_map_01 — replace main guard nodes**

In `scripts/layout_flows.py`, in the `col_map_01()` function (around line 954), replace:

```python
        "grd_unwanted_dts": M, "grd_unwanted_dca": M, "grd_unwanted_mp3": M,
        "grd_unwanted_truehd": M, "grd_unwanted_mlp": M, "grd_unwanted_flac": M,
        "grd_unwanted_vorbis": M, "grd_unwanted_opus": M,
        "grd_unwanted_pcm": M, "grd_unwanted_wma": M, "grd_unwanted_ac3": M,
```

With:

```python
        "grd_unwanted_exact": M, "grd_unwanted_partial": M,
```

- [ ] **Step 2: Update col_map_01 — add mux-incompatible detection nodes**

After `"chk_health": M,` and before `"ffs_001": M,`, add:

```python
        "grd_has_muxincompat": M, "grd_has_safe_audio": R,
```

(Using `R` for safe_audio since it's a branch off the main path, similar to other right-branch nodes.)

- [ ] **Step 3: Update VR_NODES_01 — replace VR guard nodes**

In `VR_NODES_01` (around line 911), replace:

```python
    "grd_vr_nw_dts": M, "grd_vr_nw_dca": M, "grd_vr_nw_mp3": M,
    "grd_vr_nw_truehd": M, "grd_vr_nw_mlp": M, "grd_vr_nw_flac": M,
    "grd_vr_nw_vorbis": M, "grd_vr_nw_opus": M,
    "grd_vr_nw_pcm": M, "grd_vr_nw_wma": M,
    "grd_vr_nowanted_ac3": M, "grd_vr_hasaac": M,
```

With:

```python
    "grd_vr_unwanted_exact": M, "grd_vr_unwanted_partial": M,
    "grd_vr_hasaac": M,
```

- [ ] **Step 4: Run layout script**

Run: `python3 scripts/layout_flows.py`
Expected: `01_hevc_mp4_direct_play.json: 166 nodes ...`

- [ ] **Step 5: Run full test suite**

Run: `npm test`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/layout_flows.py flows/01_hevc_mp4_direct_play.json images/01_hevc_mp4_direct_play.svg
git commit -m "chore: update layout for 166-node flow with multi-value guards"
```

---

### Task 6: Update deploy script for plugin deployment

**Files:**
- Modify: `scripts/deploy_flow.py`
- Modify: `servers.local.json` (add `plugin_path` field)
- Modify: `servers.local.json.example` (add `plugin_path` example)

- [ ] **Step 1: Read deploy_flow.py to understand current structure**

Read `scripts/deploy_flow.py` fully to understand the deployment logic, server config loading, and HTTP API patterns.

- [ ] **Step 2: Add plugin_path to servers.local.json.example**

Add a `"plugin_path"` field to the example server config. This is the path on the Tdarr server where `LocalFlowPlugins` should be synced. Example:

```json
"plugin_path": "/path/to/Tdarr/Plugins/FlowPlugins/LocalFlowPlugins"
```

- [ ] **Step 3: Add plugin sync to deploy_flow.py**

Add a function that, for each server with a `plugin_path` configured, copies the local `plugins/LocalFlowPlugins/` directory contents to the server's plugin path. Use `rsync` or `scp` via subprocess if the path is remote, or direct file copy if local/mounted.

Since the Tdarr servers are Docker containers on the Synology NAS, the `plugin_path` will likely be a local mount point. Use `shutil.copytree` with `dirs_exist_ok=True` for local paths.

Add a `--plugins` flag to only deploy plugins without deploying the flow, and make plugin deployment run automatically before flow deployment by default (can be skipped with `--no-plugins`).

- [ ] **Step 4: Commit**

```bash
git add scripts/deploy_flow.py servers.local.json.example
git commit -m "feat: add plugin deployment to deploy_flow.py"
```

---

### Task 7: Final integration test and cleanup

**Files:**
- All modified files from previous tasks

- [ ] **Step 1: Run full test suite**

Run: `npm test`
Expected: All tests PASS (0 failures)

- [ ] **Step 2: Verify node count**

Run: `node -e "const f=JSON.parse(require('fs').readFileSync('flows/01_hevc_mp4_direct_play.json','utf8')); console.log(f.flowPlugins.length, 'nodes', f.flowEdges.length, 'edges')"`
Expected: `166 nodes` (182 - 22 + 6 = 166). Edge count will vary.

- [ ] **Step 3: Verify no old guard nodes remain**

Run: `node -e "const f=JSON.parse(require('fs').readFileSync('flows/01_hevc_mp4_direct_play.json','utf8')); const ids=f.flowPlugins.map(p=>p.id); const old=['grd_unwanted_dts','grd_unwanted_dca','grd_vr_nw_dts','grd_vr_nw_dca','grd_vr_nowanted_ac3']; old.forEach(id=>{if(ids.includes(id))console.log('STILL EXISTS:',id)}); console.log('Check done')"`
Expected: `Check done` (no "STILL EXISTS" lines)

- [ ] **Step 4: Verify no dangling edges**

Run: `npm test` — the existing `'all edge sources reference a valid plugin ID'` and `'all edge targets reference a valid plugin ID'` tests catch dangling edges.

- [ ] **Step 5: Commit any final fixes, push branch, create PR**

```bash
git push -u origin claude/multi-value-stream-check
gh pr create --title "Replace guard chains with multi-value stream check plugin" --body "$(cat <<'EOF'
## Summary
- New local plugin `checkStreamPropertyMultiValue` — checks stream properties against comma-separated value lists with `equals` (exact) or `includes` (substring) conditions
- Main unwanted audio guard: 11 single-value nodes → 2 multi-value nodes
- VR unwanted audio guard: 11 single-value nodes → 2 multi-value nodes
- Mux-incompatible-only audio detection: 2 new nodes route files with only wmav2/adpcm/vorbis/opus audio to manual review instead of fail_no_streams
- Flow: 182 → 166 nodes (-16)

## Test plan
- [x] Plugin unit tests (equals, includes, stream filtering, dot-notation, edge cases)
- [x] Permutation matrix: all existing scenarios route identically
- [x] Permutation matrix: new mux-incompatible test cases (wmv/wmav2, mkv/adpcm, mixed safe+incompatible)
- [x] Flow validation: new node configs, edge wiring, old guards removed
- [x] Layout regenerated
- [ ] Deploy plugin to servers, deploy flow, requeue affected files

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
