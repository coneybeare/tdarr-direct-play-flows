/**
 * Permutation Matrix — Automated Flow Routing Tests
 *
 * Walks the flow graph for each known input permutation and asserts
 * that the file is routed correctly through guards and pipeline.
 *
 * This catches regressions when flow structure changes — if a new node
 * or edge alters the routing for a known permutation, the test fails.
 *
 * Limitations:
 *   - Simulates decision nodes against ORIGINAL file streams; does not
 *     model FFmpeg execution or stream-state changes between passes.
 *   - compareFileSizeRatio / compareFileDurationRatio always return "pass".
 *   - checkNodeHardwareEncoder defaults to NVENC available.
 */

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const flow = JSON.parse(
  readFileSync(join(__dirname, '..', 'flows', '01_hevc_mp4_direct_play.json'), 'utf8')
);

// ── Graph setup ────────────────────────────────────────────────────
const plugins = new Map(flow.flowPlugins.map((p) => [p.id, p]));
const outEdges = {};
for (const e of flow.flowEdges) {
  if (!outEdges[e.source]) outEdges[e.source] = [];
  outEdges[e.source].push({ handle: e.sourceHandle, target: e.target });
}

// ── Helpers ────────────────────────────────────────────────────────
function getNestedProp(obj, path) {
  return path.split('.').reduce((o, k) => (o != null ? o[k] : undefined), obj);
}

// ── Node evaluator ────────────────────────────────────────────────
function evaluateNode(node, file, vars) {
  const db = node.inputsDB || {};

  switch (node.pluginName) {
    case 'checkFileExtension': {
      const exts = db.extensions.split(',').map((s) => s.trim());
      return exts.includes(file.container) ? '1' : '2';
    }

    case 'checkVideoCodec': {
      const vid = file.streams.find((s) => s.codec_type === 'video');
      return vid && vid.codec_name === db.codec ? '1' : '2';
    }

    // checkStreamProperty condition behavior in Tdarr v2.62.01:
    //   Neither "includes" nor "equals" splits comma-separated valuesToMatch.
    //   The ENTIRE valuesToMatch string is used as a single value.
    //   "includes": propStr.includes(valuesToMatch) — substring match against full string
    //   "equals":   propStr === valuesToMatch — exact match against full string
    //   This means multi-value comma lists NEVER match individual codecs.
    //   Each unwanted codec needs its own guard node with a single value.
    case 'checkStreamProperty': {
      const streams = file.streams.filter(
        (s) => !db.streamType || s.codec_type === db.streamType
      );
      for (const stream of streams) {
        const prop = getNestedProp(stream, db.propertyToCheck);
        if (prop == null) continue;
        const propStr = String(prop);
        if (db.condition === 'includes' && propStr.includes(db.valuesToMatch)) return '1';
        if (db.condition === 'equals' && propStr === db.valuesToMatch) return '1';
      }
      return '2';
    }

    // checkChannelCount uses exact-match (== N channels).
    // This is required by the flow design: grd_eac3_ch (==6) falls through
    // to grd_eac3_ch8 (==8) for 7.1 sources. With >=, the ch8 node would
    // be unreachable.
    case 'checkChannelCount': {
      const target = parseInt(db.channelCount, 10);
      const audio = file.streams.filter((s) => s.codec_type === 'audio');
      return audio.some((s) => s.channels === target) ? '1' : '2';
    }

    case 'checkFileNameIncludes': {
      const terms = db.terms.split(',').map((s) => s.trim());
      const fp = file.filePath || '';
      return terms.some((t) => fp.includes(t)) ? '1' : '2';
    }

    case 'checkOverallBitrate': {
      let br = file.bitrateKbps || 5000;
      if (db.unit === 'mbps') br /= 1000;
      if (db.unit === 'bps') br *= 1000;
      return br > parseFloat(db.greaterThan) && br < parseFloat(db.lessThan)
        ? '1'
        : '2';
    }

    case 'checkVideoResolution': {
      const h = (file.streams.find((s) => s.codec_type === 'video') || {}).height || 0;
      if (h <= 360) return '1';
      if (h <= 480) return '2';
      if (h <= 576) return '3';
      if (h <= 1080) return '4';
      if (h <= 1440) return '5';
      if (h <= 2160) return '6';
      if (h <= 4320) return '7';
      return '8';
    }

    case 'checkNodeHardwareEncoder':
      return file.hasNvenc !== false ? '1' : '2';

    case 'checkFlowVariable': {
      const varName = db.variable.replace('args.variables.user.', '');
      const actual = vars[varName];
      if (db.condition === '==') return actual === db.value ? '1' : '2';
      if (db.condition === '!=') return actual !== db.value ? '1' : '2';
      return '2';
    }

    case 'setFlowVariable':
      vars[db.variable] = db.value;
      return '1';

    case 'compareFileSizeRatio':
    case 'compareFileDurationRatio':
      return '1'; // Assume validation passes for routing tests

    default:
      return '1'; // Transform nodes always follow handle "1"
  }
}

// ── Flow walker ───────────────────────────────────────────────────
const startNodeId = flow.flowPlugins.find((p) => p.pluginName === 'inputFile').id;

function walkFlow(mockFile) {
  const visited = [];
  const vars = {};
  let current = startNodeId;
  let steps = 0;

  while (current && steps < 300) {
    visited.push(current);
    const node = plugins.get(current);
    if (!node) break;
    const edges = outEdges[current] || [];
    if (edges.length === 0) break; // terminal node

    const handle = evaluateNode(node, mockFile, vars);
    const next = edges.find((e) => e.handle === handle);
    current = next ? next.target : null;
    steps++;
  }

  return visited;
}

// ── Mock file factories ───────────────────────────────────────────
function vid(codec, opts = {}) {
  return {
    codec_type: 'video',
    codec_name: codec,
    codec_tag_string: opts.tag || (codec === 'hevc' ? 'hvc1' : ''),
    height: opts.height || 1080,
  };
}

function aud(codec, channels, lang = 'eng') {
  return {
    codec_type: 'audio',
    codec_name: codec,
    channels,
    tags: { language: lang },
  };
}

function file(container, streams, opts = {}) {
  return {
    container,
    filePath: opts.filePath || `/media/Movie.${container}`,
    bitrateKbps: opts.bitrateKbps || 5000,
    hasNvenc: opts.hasNvenc !== false,
    streams,
  };
}

// ── Assertion helpers ─────────────────────────────────────────────
function has(path, nodeId, msg) {
  assert.ok(path.includes(nodeId), msg || `Expected ${nodeId} in path`);
}

function lacks(path, nodeId, msg) {
  assert.ok(!path.includes(nodeId), msg || `Expected ${nodeId} NOT in path`);
}

function assertSkip(path) {
  has(path, 'fl_noop', 'Should skip via fl_noop');
  lacks(path, 'ffs_001', 'Should not enter normal pipeline');
  lacks(path, 'ffs_vr', 'Should not enter VR pipeline');
}

function assertProcess(path) {
  has(path, 'ffs_001', 'Should enter normal pipeline');
  has(path, 'fl_replace', 'Should reach fl_replace');
}

function assertProcessVR(path) {
  has(path, 'ffs_vr', 'Should enter full VR pipeline');
  has(path, 'fl_vr_replace', 'Should reach fl_vr_replace');
  lacks(path, 'ffs_001', 'Should not enter normal pipeline');
  lacks(path, 'ffs_vr_retag', 'Should not enter VR retag shortcut');
}

function assertProcessVRRetag(path) {
  has(path, 'ffs_vr_retag', 'Should enter VR retag pipeline');
  has(path, 'ffe_vr_retag', 'Should execute VR retag');
  has(path, 'fl_vr_replace', 'Should reach fl_vr_replace');
  lacks(path, 'ffs_vr', 'Should NOT enter full VR pipeline');
  lacks(path, 'ffs_001', 'Should not enter normal pipeline');
}

function assertEAC3(path) {
  const ok = path.includes('cmd_eac3_eng') || path.includes('cmd_eac3_fb');
  assert.ok(ok, `Should create EAC3 (via eng or fallback). Path: ${path.join(' → ')}`);
}

function assertNoEAC3(path) {
  lacks(path, 'cmd_eac3_eng', 'Should NOT create EAC3 (eng)');
  lacks(path, 'cmd_eac3_fb', 'Should NOT create EAC3 (fallback)');
}

function assertAAC(path) {
  const ok = path.includes('cmd_ens_eng') ||
    path.includes('cmd_ens_und') ||
    path.includes('cmd_ens_fb');
  assert.ok(ok, 'Should create stereo AAC via EnsureAudioStream');
}

function assertPass2(path) {
  has(path, 'chk_health_002', 'Should health-check before pass 2');
  has(path, 'ffs_002', 'Should enter pass 2');
  has(path, 'cmd_reorder_002', 'Should map streams in pass 2');
  has(path, 'cmd_faststart2', 'Should apply faststart in pass 2');
}

function assertFallbackAAC(path) {
  lacks(path, 'cmd_ens_eng', 'Should skip eng AAC');
  lacks(path, 'cmd_ens_und', 'Should skip und AAC');
  has(path, 'cmd_ens_fb', 'Should use fallback AAC');
}

// ── Tests ─────────────────────────────────────────────────────────
describe('Permutation Matrix — Flow Routing', () => {

  // ────────────────────────────────────────────────────────────────
  // Category 1: MP4/HEVC already optimal
  // ────────────────────────────────────────────────────────────────
  describe('1. MP4/HEVC already optimal', () => {
    test('1a: mp4/hevc(hvc1) + aac 2.0 — skip', () => {
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 2)]));
      assertSkip(path);
    });

    test('1b: mp4/hevc(hvc1) + aac 2.0 + eac3 5.1 — skip (already optimal)', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc'),
        aud('aac', 2),
        aud('eac3', 6),
      ]));
      assertSkip(path);
    });

    test('1c: mp4/hevc(hev1) + aac 2.0 — process (retag to hvc1)', () => {
      const path = walkFlow(file('mp4', [vid('hevc', { tag: 'hev1' }), aud('aac', 2)]));
      assertProcess(path);
      has(path, 'grd_tag', 'Should reach hvc1 tag check');
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 2: MKV with HEVC video
  // ────────────────────────────────────────────────────────────────
  describe('2. MKV with HEVC video', () => {
    test('2a: mkv/hevc/ac3 5.1 — remux, EAC3+AAC, pass 2 rm AC3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('2b: mkv/hevc/ac3 5.1 + aac 2.0 — remux, EAC3, pass 2 rm AC3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6), aud('aac', 2)]));
      assertProcess(path);
      assertEAC3(path);
      assertPass2(path);
    });

    test('2c: mkv/hevc/dts 5.1 — remux, rm DTS, create EAC3+AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('dts', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('2d: mkv/hevc/truehd 7.1 — remux, rm TrueHD, create EAC3+AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('truehd', 8)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('2e: mkv/hevc/aac 2.0 + subs — remux, strip subs', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertPass2(path);
    });

    test('2f: mkv/hevc/flac 2.0 — remux, rm FLAC, create AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('flac', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('2g: mkv/hevc/ac3 2.0 — remux, create AAC, rm AC3 (no EAC3 — <6ch)', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('2h: mkv/hevc/mp3 2.0 — remux, create AAC, rm MP3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('mp3', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 3: MKV with non-HEVC video (needs transcode)
  // ────────────────────────────────────────────────────────────────
  describe('3. MKV with non-HEVC video', () => {
    test('3a: mkv/h264/ac3 5.1 — transcode, EAC3+AAC, rm AC3', () => {
      const path = walkFlow(file('mkv', [vid('h264'), aud('ac3', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('3b: mkv/h264/aac 2.0 — transcode, keep AAC', () => {
      const path = walkFlow(file('mkv', [vid('h264'), aud('aac', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertPass2(path);
    });

    test('3c: mkv/mpeg2/ac3 2.0 — transcode, create AAC, rm AC3', () => {
      const path = walkFlow(file('mkv', [vid('mpeg2video', { height: 480 }), aud('ac3', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('3d: mkv/vp9/opus 2.0 — transcode, rm Opus, create AAC', () => {
      const path = walkFlow(file('mkv', [vid('vp9'), aud('opus', 2)]));
      assertProcess(path);
      assertNoEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('3e: mkv/av1/aac 2.0 — software transcode (AV1 can\'t hw decode), keep AAC', () => {
      const path = walkFlow(file('mkv', [vid('av1'), aud('aac', 2)]));
      assertProcess(path);
      has(path, 'grd_av1', 'AV1 guard should be visited');
      has(path, 'cmd_hevc_sw', 'AV1 must use software encoder (T400 can\'t hw decode AV1)');
      lacks(path, 'chk_nvenc', 'AV1 should bypass NVENC entirely');
      lacks(path, 'chk_resolution', 'AV1 should skip NVENC resolution tier');
      assertNoEAC3(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 4: AVI files
  // ────────────────────────────────────────────────────────────────
  describe('4. AVI files', () => {
    test('4a: avi/h264/mp3 2.0 — transcode, create AAC, rm MP3', () => {
      const path = walkFlow(file('avi', [vid('h264', { height: 480 }), aud('mp3', 2)]));
      assertProcess(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('4b: avi/mpeg4/ac3 5.1 — transcode, EAC3+AAC, rm AC3', () => {
      const path = walkFlow(file('avi', [vid('mpeg4', { height: 480 }), aud('ac3', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 5: M2TS/TS files
  // ────────────────────────────────────────────────────────────────
  describe('5. M2TS/TS files', () => {
    test('5a: m2ts/h264/dts-hd 7.1 — transcode, rm DTS, create EAC3+AAC', () => {
      const path = walkFlow(file('m2ts', [vid('h264'), aud('dts', 8)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('5b: ts/h264/ac3 5.1 — transcode, EAC3+AAC, rm AC3', () => {
      const path = walkFlow(file('ts', [vid('h264'), aud('ac3', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 6: Multi-language audio
  // ────────────────────────────────────────────────────────────────
  describe('6. Multi-language audio', () => {
    test('6a: mkv/hevc/ac3 5.1 eng + ac3 5.1 jpn — EAC3+AAC(eng), rm AC3', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc'),
        aud('ac3', 6, 'eng'),
        aud('ac3', 6, 'jpn'),
      ]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('6b: mkv/hevc/aac 2.0 und — keep existing', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2, 'und')]));
      assertProcess(path);
      assertNoEAC3(path);
      // eng AAC skipped (no eng audio), und AAC created
      lacks(path, 'cmd_ens_eng', 'No eng → skip eng AAC');
      has(path, 'cmd_ens_und', 'Has und → create und AAC');
      assertPass2(path);
    });

    test('6c: mkv/hevc/ac3 5.1 eng + aac 2.0 und — EAC3(eng) + keep AAC(und), rm AC3', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc'),
        aud('ac3', 6, 'eng'),
        aud('aac', 2, 'und'),
      ]));
      assertProcess(path);
      assertEAC3(path);
      assertPass2(path);
    });

    test('6d: mkv/hevc/aac 2.0 jpn (no eng, no und) — fallback AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2, 'jpn')]));
      assertProcess(path);
      assertFallbackAAC(path);
      assertPass2(path);
    });

    // Known limitation: EnsureAudioStream language fallback uses "en" (loadDefaultValues
    // replaces ""), then "und" — neither matches "swe"/"cze". Routing is correct (reaches
    // fallback nodes) but audio creation silently fails at runtime. See PERMUTATION_MATRIX.md.
    test('6e: mkv/h264/dts 5.1 swe (no eng, no und) — routes to fallback (known limitation)', () => {
      const path = walkFlow(file('mkv', [vid('h264'), aud('dts', 6, 'swe')]));
      assertProcess(path);
      assertFallbackAAC(path);
      assertPass2(path);
    });

    test('6f: mp4/hevc(hvc1)/ac3 5.1 cze (no eng, no und) — routes to fallback (known limitation)', () => {
      const path = walkFlow(file('mp4', [vid('hevc', { tag: 'hvc1' }), aud('ac3', 6, 'cze')]));
      assertProcess(path);
      assertFallbackAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 7: Many-stream files (walker uses same streams; sub/font
  // count doesn't affect routing decisions)
  // ────────────────────────────────────────────────────────────────
  describe('7. Many-stream files', () => {
    test('7a: mkv/hevc/ac3 5.1 + subs — EAC3+AAC, pass 2 rm AC3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6)]));
      assertProcess(path);
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('7b: mkv/h264/dts 5.1 + ac3 5.1 + subs — EAC3+AAC, pass 2 rm AC3', () => {
      const path = walkFlow(file('mkv', [
        vid('h264'),
        aud('dts', 6),
        aud('ac3', 6),
      ]));
      assertProcess(path);
      // AC3 present → grd_eac3_codec matches → EAC3 created from AC3
      assertEAC3(path);
      assertAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 8: DoVi / HDR files
  // ────────────────────────────────────────────────────────────────
  describe('8. DoVi/HDR files', () => {
    test('8a: mkv/hevc(dvh1)/eac3 5.1 — skip (DoVi detected via non-MP4 guard)', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc', { tag: 'dvh1' }),
        aud('eac3', 6),
      ]));
      has(path, 'grd_dovi_non_mp4', 'Non-MP4 DoVi guard should be reached');
      assertSkip(path);
    });

    test('8a-mp4: mp4/hevc(dvh1)/eac3 5.1 — skip (DoVi detected)', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc', { tag: 'dvh1' }),
        aud('eac3', 6),
      ]));
      assertSkip(path);
      has(path, 'grd_dovi', 'DoVi guard should be reached');
    });

    test('8b: mp4/hevc(dvhe)/aac 2.0 — skip (DoVi detected)', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc', { tag: 'dvhe' }),
        aud('aac', 2),
      ]));
      assertSkip(path);
      has(path, 'grd_dovi', 'DoVi guard should be reached');
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 9: VR files
  // ────────────────────────────────────────────────────────────────
  describe('9. VR files', () => {
    test('9a: mkv/hevc/aac 2.0 in /Virtual Reality/ — VR remux', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc', { height: 2160 }),
        aud('aac', 2),
      ], { filePath: '/media/Virtual Reality/Movie.mkv' }));
      assertProcessVR(path);
      has(path, 'chk_vr', 'Should reach VR detection');
      lacks(path, 'cmd_eac3_eng', 'VR has no EAC3 section');
    });

    test('9b: mkv/h264/aac 2.0 in /Virtual Reality/ — VR transcode', () => {
      const path = walkFlow(file('mkv', [
        vid('h264', { height: 2160 }),
        aud('aac', 2),
      ], { filePath: '/media/Virtual Reality/Movie.mkv' }));
      assertProcessVR(path);
    });

    test('9c: mp4/hevc(hev1)/aac 2.0 in /Virtual Reality/ — VR retag only', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc', { tag: 'hev1', height: 2160 }),
        aud('aac', 2),
      ], { filePath: '/media/Virtual Reality/Movie.mp4' }));
      assertProcessVRRetag(path);
      has(path, 'grd_vr_ismp4', 'Should check MP4');
      has(path, 'grd_vr_ishevc', 'Should check HEVC');
      has(path, 'grd_vr_hasaac', 'Should check AAC');
      has(path, 'cmt_vr_retag', 'Should enter retag path');
    });

    test('9d: mkv/hevc(hev1)/aac 2.0 in /Virtual Reality/ — full VR (not MP4)', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc', { tag: 'hev1', height: 2160 }),
        aud('aac', 2),
      ], { filePath: '/media/Virtual Reality/Movie.mkv' }));
      assertProcessVR(path);
      has(path, 'grd_vr_ismp4', 'Should check MP4');
      lacks(path, 'cmt_vr_retag', 'Should NOT enter retag path');
    });

    test('9e: mp4/h264/aac 2.0 in /Virtual Reality/ — full VR (not HEVC)', () => {
      const path = walkFlow(file('mp4', [
        vid('h264', { height: 2160 }),
        aud('aac', 2),
      ], { filePath: '/media/Virtual Reality/Movie.mp4' }));
      assertProcessVR(path);
      has(path, 'grd_vr_ishevc', 'Should check HEVC');
      lacks(path, 'cmt_vr_retag', 'Should NOT enter retag path');
    });

    test('9f: mp4/hevc(hev1)/dts 6ch in /Virtual Reality/ — full VR (unwanted audio)', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc', { tag: 'hev1', height: 2160 }),
        aud('dts', 6),
      ], { filePath: '/media/Virtual Reality/Movie.mp4' }));
      assertProcessVR(path);
      has(path, 'grd_vr_nw_dts', 'Should check unwanted audio');
      lacks(path, 'cmt_vr_retag', 'Should NOT enter retag path');
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Category 10: Edge cases
  // ────────────────────────────────────────────────────────────────
  describe('10. Edge cases', () => {
    test('10a: mkv/hevc/ac3 5.1 + eac3 5.1 (orphaned) — process, rm AC3', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc'),
        aud('ac3', 6),
        aud('eac3', 6),
      ]));
      assertProcess(path);
      assertPass2(path);
    });

    test('10b: mp4/hevc(hvc1)/no audio — fail_no_streams', () => {
      const path = walkFlow(file('mp4', [vid('hevc')]));
      // No audio → grd_aud fails → process → pipeline → grd_has_audio → fail
      has(path, 'fail_no_streams', 'Should fail with no audio');
    });

    test('10c: mkv/hevc/pcm_s24le 2.0 — rm PCM, create AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('pcm_s24le', 2)]));
      assertProcess(path);
      assertAAC(path);
      assertPass2(path);
    });

    test('10d: mkv/hevc/ac3 5.1 + mp3 2.0 — EAC3+AAC, rm AC3+MP3', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc'),
        aud('ac3', 6),
        aud('mp3', 2),
      ]));
      assertProcess(path);
      assertEAC3(path);
      assertPass2(path);
    });

    test('10e: mp4/hevc(hvc1)/aac 2.0 + mp3 2.0 — process (guard catches unwanted mp3)', () => {
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 2), aud('mp3', 2)]));
      has(path, 'grd_unwanted_mp3', 'Should reach MP3 guard in unwanted chain');
      assertProcess(path);
      assertPass2(path);
    });

    test('10f: mp4/hevc(hvc1)/aac 2.0 + ac3 2.0 — process (guard catches unwanted ac3)', () => {
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 2), aud('ac3', 2)]));
      has(path, 'grd_unwanted_ac3', 'Should reach AC3 guard');
      assertProcess(path);
      assertPass2(path);
    });

    test('10i: mp4/hevc(hvc1)/aac 2.0 + eac3 6ch + ac3 6ch — pass 2 removes AC3 (regression)', () => {
      // Exact profile from production failures: "almost optimal" except legacy AC3 surround.
      // Two regressions fixed here:
      // 1. ffmpegCommandStart does not map streams — cmd_reorder_002 (RorderStreams) required
      // 2. condition "equals" does not exist in RemoveStreamByProperty — falls through to
      //    not_includes which INVERTS removal (removes non-matching, keeps matching).
      //    Fix: cmd_rm_ac3 uses codec_tag_string "ac-3" with condition "includes" (MP4
      //    tags "ac-3" vs "ec-3" have no substring overlap, preserving EAC3).
      const path = walkFlow(file('mp4', [
        vid('hevc'),
        aud('aac', 2),
        aud('eac3', 6),
        aud('ac3', 6),
      ]));
      has(path, 'grd_unwanted_ac3', 'Should reach AC3 guard in unwanted chain');
      assertProcess(path);
      assertEAC3(path);
      assertPass2(path);
    });

    test('10j: mp4/hevc(hvc1)/aac 2.0 + mp3 2.0 + ac3 6ch — pass 2 removes both AC3 and MP3', () => {
      // File with both legacy codecs: AC3 surround + MP3 stereo. Pass 2 must remove both.
      const path = walkFlow(file('mp4', [
        vid('hevc'),
        aud('aac', 2),
        aud('mp3', 2),
        aud('ac3', 6),
      ]));
      has(path, 'grd_unwanted_mp3', 'Should reach MP3 guard in unwanted chain');
      assertProcess(path);
      assertEAC3(path);
      assertPass2(path);
      has(path, 'cmd_rm_ac3', 'Pass 2 must traverse AC3 removal node');
      has(path, 'cmd_rm_mp3', 'Pass 2 must traverse MP3 removal node');
    });

    test('10g: mp4/hevc(hvc1)/aac 2.0 + eac3 6ch — skip (eac3 surround is NOT unwanted)', () => {
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 2), aud('eac3', 6)]));
      // Has surround → grd_surr_ch YES → unwanted chain → should NOT match eac3
      has(path, 'grd_unwanted_dts');
      has(path, 'grd_unwanted_ac3');
      assertSkip(path);
    });

    test('10h: mp4/hevc(hvc1)/eac3 6ch only (no AAC, non-eng) — process, create AAC', () => {
      const path = walkFlow(file('mp4', [
        vid('hevc'),
        aud('eac3', 6, 'kor'),
        aud('eac3', 6, 'chi'),
      ]));
      // grd_aud detects no AAC → routes to processing
      has(path, 'grd_aud', 'Should reach AAC guard');
      lacks(path, 'grd_ch', 'Should not pass AAC guard (no AAC present)');
      assertProcess(path);
      assertFallbackAAC(path);
      assertPass2(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Guard chain: detailed routing verification
  // ────────────────────────────────────────────────────────────────
  describe('Guard chain detail', () => {
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
      has(path, 'grd_unwanted_dts');
      has(path, 'grd_unwanted_ac3');
      has(path, 'cmt_optimal');
      has(path, 'fl_noop');
    });

    test('mp4/hevc(hvc1)/aac 2.0 but no 2ch stereo → process', () => {
      // File has AAC but it's 5.1, not stereo
      const path = walkFlow(file('mp4', [vid('hevc'), aud('aac', 6)]));
      has(path, 'grd_ch');
      // No 2ch → grd_ch fails → cmt_proc
      lacks(path, 'grd_surr_ch', 'Should not reach surround check');
      assertProcess(path);
    });

    test('mp4/h264 → process (not HEVC)', () => {
      const path = walkFlow(file('mp4', [vid('h264'), aud('aac', 2)]));
      has(path, 'grd_vid');
      lacks(path, 'grd_dovi', 'Should not reach DoVi check');
      assertProcess(path);
    });

    test('mkv → process immediately (not mp4)', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2)]));
      has(path, 'grd_ext');
      lacks(path, 'grd_vid', 'MKV skips rest of guard chain');
      assertProcess(path);
    });

    test('orphaned EAC3 (stereo, no surround) → process', () => {
      // MP4/HEVC/hvc1 with AAC 2.0 + EAC3 2.0 (stereo EAC3 = orphaned)
      const path = walkFlow(file('mp4', [
        vid('hevc'),
        aud('aac', 2),
        aud('eac3', 2),
      ]));
      has(path, 'grd_surr_ch');
      // No 6ch surround → grd_surr_ch fails → grd_has_eac3
      has(path, 'grd_has_eac3');
      // Has EAC3 → orphaned → process
      assertProcess(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // EAC3 section: detailed routing
  // ────────────────────────────────────────────────────────────────
  describe('EAC3 section detail', () => {
    test('AC3 5.1 eng → creates EAC3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6)]));
      has(path, 'grd_eac3_ch');
      has(path, 'grd_eac3_has_eng');
      has(path, 'cmd_eac3_eng');
    });

    test('AC3 7.1 (8ch) eng → creates EAC3 via ch8 path', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 8)]));
      has(path, 'grd_eac3_ch');
      // Not exactly 6ch → falls through to ch8 check
      has(path, 'grd_eac3_ch8');
      has(path, 'grd_eac3_has_eng');
      has(path, 'cmd_eac3_eng');
    });

    test('AC3 2.0 → EAC3 section skips (not enough channels)', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 2)]));
      has(path, 'grd_eac3_ch');
      has(path, 'grd_eac3_ch8');
      has(path, 'cmd_rm_eac3', 'Should clean up any orphaned EAC3');
      lacks(path, 'cmd_eac3_eng');
    });

    test('AC3 5.1 with no English audio → fallback EAC3 creation', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6, 'jpn')]));
      has(path, 'grd_eac3_has_eng');
      lacks(path, 'cmd_eac3_eng', 'No eng audio → skip eng EAC3');
      has(path, 'cmd_eac3_fb', 'No eng audio → fallback EAC3');
      assertEAC3(path);
    });

    test('DTS 5.1 → EAC3 channel check matches, creates EAC3', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('dts', 6)]));
      has(path, 'grd_eac3_ch', 'DTS 6ch enters EAC3 section via channel check');
      assertEAC3(path);
    });
  });

  // ────────────────────────────────────────────────────────────────
  // Stereo AAC section: language routing
  // ────────────────────────────────────────────────────────────────
  describe('Stereo AAC section detail', () => {
    test('eng audio → eng AAC created, und AAC skipped (no und)', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('ac3', 6, 'eng')]));
      has(path, 'grd_has_eng');
      has(path, 'cmd_ens_eng');
      has(path, 'grd_dup_und');
      lacks(path, 'cmd_ens_und', 'No und audio → skip und AAC');
    });

    test('eng + und audio → both AAC tracks created', () => {
      const path = walkFlow(file('mkv', [
        vid('hevc'),
        aud('ac3', 6, 'eng'),
        aud('aac', 2, 'und'),
      ]));
      has(path, 'cmd_ens_eng');
      has(path, 'cmd_ens_und');
    });

    test('und only (no eng) → skip eng, create und AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2, 'und')]));
      lacks(path, 'cmd_ens_eng');
      has(path, 'grd_dup_und');
      has(path, 'cmd_ens_und');
    });

    test('jpn only (no eng, no und) → fallback AAC', () => {
      const path = walkFlow(file('mkv', [vid('hevc'), aud('aac', 2, 'jpn')]));
      assertFallbackAAC(path);
    });
  });
});
