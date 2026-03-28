import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const flowsDir = join(__dirname, '..', 'flows');

const PLUGIN_REQUIRED_FIELDS = ['name', 'sourceRepo', 'pluginName', 'version', 'id', 'position', 'inputsDB', 'fpEnabled'];
const EDGE_REQUIRED_FIELDS = ['source', 'sourceHandle', 'target', 'id'];
const FLOW_REQUIRED_KEYS = ['name', 'description', 'tags', 'flowPlugins', 'flowEdges'];

const flowFiles = readdirSync(flowsDir)
  .filter((f) => f.endsWith('.json'))
  .sort();

assert.ok(flowFiles.length > 0, 'No flow JSON files found in flows/');

for (const file of flowFiles) {
  const filePath = join(flowsDir, file);
  const content = readFileSync(filePath, 'utf8');

  describe(file, () => {
    test('is valid JSON', () => {
      assert.doesNotThrow(() => JSON.parse(content), `${file} contains invalid JSON`);
    });

    const flow = JSON.parse(content);

    test('has required top-level keys', () => {
      for (const key of FLOW_REQUIRED_KEYS) {
        assert.ok(key in flow, `Missing top-level key: "${key}"`);
      }
    });

    test('flowPlugins is a non-empty array', () => {
      assert.ok(Array.isArray(flow.flowPlugins), 'flowPlugins must be an array');
      assert.ok(flow.flowPlugins.length > 0, 'flowPlugins must not be empty');
    });

    test('flowEdges is an array', () => {
      assert.ok(Array.isArray(flow.flowEdges), 'flowEdges must be an array');
    });

    test('each plugin has required fields', () => {
      for (const plugin of flow.flowPlugins) {
        for (const field of PLUGIN_REQUIRED_FIELDS) {
          assert.ok(
            field in plugin,
            `Plugin "${plugin.id ?? plugin.name}" is missing required field: "${field}"`
          );
        }
      }
    });

    test('all plugins have a valid sourceRepo', () => {
      const VALID_REPOS = new Set(['Community', 'local']);
      for (const plugin of flow.flowPlugins) {
        assert.ok(
          VALID_REPOS.has(plugin.sourceRepo),
          `Plugin "${plugin.id}" has sourceRepo "${plugin.sourceRepo}", expected one of: ${[...VALID_REPOS].join(', ')}`
        );
      }
    });

    test('no duplicate plugin IDs', () => {
      const seen = new Set();
      for (const plugin of flow.flowPlugins) {
        assert.ok(!seen.has(plugin.id), `Duplicate plugin ID: "${plugin.id}"`);
        seen.add(plugin.id);
      }
    });

    test('all inputsDB values are strings', () => {
      for (const plugin of flow.flowPlugins) {
        for (const [key, value] of Object.entries(plugin.inputsDB)) {
          assert.strictEqual(
            typeof value,
            'string',
            `Plugin "${plugin.id}" inputsDB.${key} is ${typeof value}, expected string`
          );
        }
      }
    });

    test('each edge has required fields', () => {
      for (const edge of flow.flowEdges) {
        for (const field of EDGE_REQUIRED_FIELDS) {
          assert.ok(
            field in edge,
            `Edge "${edge.id ?? '(unknown)'}" is missing required field: "${field}"`
          );
        }
      }
    });

    test('no duplicate edge IDs', () => {
      const seen = new Set();
      for (const edge of flow.flowEdges) {
        assert.ok(!seen.has(edge.id), `Duplicate edge ID: "${edge.id}"`);
        seen.add(edge.id);
      }
    });

    const nodeIds = new Set(flow.flowPlugins.map((p) => p.id));

    test('all edge sources reference a valid plugin ID', () => {
      for (const edge of flow.flowEdges) {
        assert.ok(
          nodeIds.has(edge.source),
          `Edge "${edge.id}" source "${edge.source}" does not reference a known plugin ID`
        );
      }
    });

    test('all edge targets reference a valid plugin ID', () => {
      for (const edge of flow.flowEdges) {
        assert.ok(
          nodeIds.has(edge.target),
          `Edge "${edge.id}" target "${edge.target}" does not reference a known plugin ID`
        );
      }
    });

    test('flow has an inputFile entry point', () => {
      const hasInput = flow.flowPlugins.some((p) => p.pluginName === 'inputFile');
      assert.ok(hasInput, 'Flow is missing an inputFile node');
    });

    test('flow has an onFlowError handler', () => {
      const hasErrorHandler = flow.flowPlugins.some((p) => p.pluginName === 'onFlowError');
      assert.ok(hasErrorHandler, 'Flow is missing an onFlowError node');
    });

    test('bitrate cap chain is wired correctly', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));

      // NVENC encoders → chk_br_vlow
      for (const enc of ['cmd_hevc_sd', 'cmd_hevc_1080', 'cmd_hevc_4k']) {
        assert.strictEqual(edgeMap.get(`${enc}:1`), 'chk_br_vlow', `${enc} should route to chk_br_vlow`);
      }

      // chk_br_vlow → cmd_cap_vlow (yes) / chk_br_low (no)
      assert.strictEqual(edgeMap.get('chk_br_vlow:1'), 'cmd_cap_vlow');
      assert.strictEqual(edgeMap.get('chk_br_vlow:2'), 'chk_br_low');

      // chk_br_low → cmd_cap_low (yes) / chk_br_mid (no)
      assert.strictEqual(edgeMap.get('chk_br_low:1'), 'cmd_cap_low');
      assert.strictEqual(edgeMap.get('chk_br_low:2'), 'chk_br_mid');

      // chk_br_mid → cmd_cap_mid (yes) / cmt_tags (no)
      assert.strictEqual(edgeMap.get('chk_br_mid:1'), 'cmd_cap_mid');
      assert.strictEqual(edgeMap.get('chk_br_mid:2'), 'cmt_tags');

      // All caps → cmt_tags
      for (const cap of ['cmd_cap_vlow', 'cmd_cap_low', 'cmd_cap_mid']) {
        assert.strictEqual(edgeMap.get(`${cap}:1`), 'cmt_tags', `${cap} should route to cmt_tags`);
      }

      // SW encoder bypasses caps → cmt_tags
      assert.strictEqual(edgeMap.get('cmd_hevc_sw:1'), 'cmt_tags');
    });

    test('bitrate cap CQ values are not below encoder QP', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const caps = [
        { id: 'cmd_cap_vlow', minCQ: 28 },
        { id: 'cmd_cap_low', minCQ: 26 },
        { id: 'cmd_cap_mid', minCQ: 24 },
      ];

      for (const { id, minCQ } of caps) {
        const node = pluginMap.get(id);
        assert.ok(node, `Missing node ${id}`);
        const match = node.inputsDB.outputArguments.match(/-cq\s+(\d+)/);
        assert.ok(match, `${id} outputArguments missing -cq`);
        const cq = parseInt(match[1], 10);
        assert.ok(cq >= minCQ, `${id} CQ ${cq} is below minimum ${minCQ} (would inflate file size)`);
      }
    });

    test('EAC3 section and audio pipeline route correctly', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_eac3_codec removed — Tdarr v2.62.01 doesn't split comma values,
      // so the old multi-codec guard never matched. Channel checks are sufficient.
      assert.ok(!pluginMap.has('grd_eac3_codec'),
        'grd_eac3_codec must be removed — comma-separated valuesToMatch never matches in Tdarr v2.62.01');

      // cmt_eac3 routes directly to channel check
      assert.strictEqual(edgeMap.get('cmt_eac3:1'), 'grd_eac3_ch',
        'EAC3 section should route directly to channel check');

      // Surround channels route through English audio guard before EAC3 creation
      assert.strictEqual(edgeMap.get('grd_eac3_ch:1'), 'grd_eac3_has_eng',
        '6+ ch surround should check for English audio before EAC3 creation');
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:1'), 'grd_eac3_has_eng',
        '8 ch surround should check for English audio before EAC3 creation');

      // English audio guard routes to EAC3 creation (eng or fallback)
      assert.strictEqual(edgeMap.get('grd_eac3_has_eng:1'), 'cmd_eac3_eng',
        'Has English audio should route to EAC3 creation');
      assert.strictEqual(edgeMap.get('grd_eac3_has_eng:2'), 'cmd_eac3_fb',
        'No English audio should route to fallback EAC3 creation');
      assert.strictEqual(edgeMap.get('cmd_eac3_fb:1'), 'cmt_reorder',
        'Fallback EAC3 should route to cmt_reorder (AAC is in pass 2)');

      // Verify fallback EAC3 config
      const eac3Fb = pluginMap.get('cmd_eac3_fb');
      assert.ok(eac3Fb, 'Missing node cmd_eac3_fb');
      assert.strictEqual(eac3Fb.pluginName, 'ffmpegCommandEnsureAudioStream');
      assert.strictEqual(eac3Fb.inputsDB.audioEncoder, 'eac3');
      assert.strictEqual(eac3Fb.inputsDB.language, '',
        'Fallback EAC3 must use empty language (any source)');

      // cmd_rm_old_eac3 must NOT exist (it destroyed source EAC3 before creation)
      assert.ok(!pluginMap.has('cmd_rm_old_eac3'),
        'cmd_rm_old_eac3 must be removed — it strips source EAC3 surround tracks before EAC3 creation');

      // Pass 2 AC3/MP3 removal uses exact-match ("equals") to avoid
      // destroying EAC3 ("eac3".includes("ac3") = true with "includes")
      assert.ok(pluginMap.has('cmd_rm_ac3'),
        'cmd_rm_ac3 must exist in pass 2');
      assert.ok(pluginMap.has('cmd_rm_mp3'),
        'cmd_rm_mp3 must exist in pass 2');
      assert.ok(!pluginMap.has('cmd_rm_ac3mp3'),
        'cmd_rm_ac3mp3 must be replaced by separate exact-match nodes');

      // EAC3 section routes to cmt_reorder (AAC stereo moved to pass 2)
      assert.strictEqual(edgeMap.get('cmd_eac3_eng:1'), 'cmt_reorder',
        'EAC3 eng path should route to cmt_reorder (AAC is in pass 2)');
      assert.strictEqual(edgeMap.get('cmd_rm_eac3:1'), 'cmt_reorder',
        'cmd_rm_eac3 should route to cmt_reorder (AAC is in pass 2)');

      // cmd_rm_eac3 on non-surround path (no 6+ch surround, strip all eac3)
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:2'), 'cmd_rm_eac3',
        'Non-surround channel path should strip EAC3 first');

      // Old second-pass nodes from pre-#49 must still be gone
      assert.ok(!pluginMap.has('ffs_reorder'),
        'ffs_reorder must be removed — old second pass');
      assert.ok(!pluginMap.has('ffe_reorder'),
        'ffe_reorder must be removed — old second pass');
      assert.ok(!pluginMap.has('cmt_reorder2'),
        'cmt_reorder2 must be removed — old second pass');

      // Pass 2 chain: ffe_001 → chk_health_002 → ffs_002 → cmd_reorder_002 → cmd_rm_ac3 → cmd_rm_mp3 → cmd_faststart2 → ffe_002 → cmt_size
      assert.strictEqual(edgeMap.get('ffe_001:1'), 'chk_health_002',
        'ffe_001 should route to health check before pass 2');
      assert.strictEqual(edgeMap.get('chk_health_002:1'), 'ffs_002',
        'Health check should route to pass 2 start');
      assert.strictEqual(edgeMap.get('chk_health_002:2'), 'fail_health2',
        'Health check failure must route to failFlow (corrupt pass 1 output)');
      assert.ok(pluginMap.has('fail_health2'),
        'fail_health2 plugin node must exist');
      assert.strictEqual(pluginMap.get('fail_health2').pluginName, 'failFlow',
        'fail_health2 must be a failFlow node');
      assert.strictEqual(edgeMap.get('ffs_002:1'), 'cmt_audio',
        'Pass 2 start should route to AAC stereo section (moved from pass 1)');
      assert.strictEqual(edgeMap.get('cmd_reorder_002:1'), 'cmd_rm_ac3',
        'Stream reorder should route to AC3 removal');
      assert.strictEqual(edgeMap.get('cmd_rm_ac3:1'), 'cmd_rm_mp3',
        'AC3 removal should route to MP3 removal');
      assert.strictEqual(edgeMap.get('cmd_rm_mp3:1'), 'cmd_faststart2',
        'MP3 removal should route to faststart');
      assert.strictEqual(edgeMap.get('cmd_faststart2:1'), 'ffe_002',
        'Faststart should route to pass 2 execute');
      assert.strictEqual(edgeMap.get('ffe_002:1'), 'cmt_size',
        'Pass 2 execute should route to size check');

      // cmd_reorder_002 maps all streams (ffmpegCommandSetContainer is a no-op on already-MP4 files)
      const reorderPass2 = pluginMap.get('cmd_reorder_002');
      assert.ok(reorderPass2, 'Missing node cmd_reorder_002');
      assert.strictEqual(reorderPass2.pluginName, 'ffmpegCommandRorderStreams',
        'cmd_reorder_002 must be a RorderStreams plugin to map all streams');
      assert.ok(reorderPass2.inputsDB, 'cmd_reorder_002 must define inputsDB to map streams');
      assert.strictEqual(typeof reorderPass2.inputsDB, 'object',
        'cmd_reorder_002 inputsDB must be an object');
      assert.ok(Object.keys(reorderPass2.inputsDB).length > 0,
        'cmd_reorder_002 inputsDB must have at least one mapping entry');
    });

    test('AAC stereo creation is in pass 2, not pass 1 (prevents -ac conflict)', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // AAC stereo nodes must NOT be reachable from pass 1 EAC3 section
      assert.notStrictEqual(edgeMap.get('cmd_eac3_eng:1'), 'cmt_audio',
        'cmd_eac3_eng must NOT route to cmt_audio (AAC is in pass 2)');
      assert.notStrictEqual(edgeMap.get('cmd_eac3_fb:1'), 'cmt_audio',
        'cmd_eac3_fb must NOT route to cmt_audio (AAC is in pass 2)');
      assert.notStrictEqual(edgeMap.get('cmd_rm_eac3:1'), 'cmt_audio',
        'cmd_rm_eac3 must NOT route to cmt_audio (AAC is in pass 2)');

      // AAC stereo section must be wired into pass 2
      assert.strictEqual(edgeMap.get('ffs_002:1'), 'cmt_audio',
        'ffs_002 must route to cmt_audio (AAC stereo section)');

      // AAC section terminals must route to cmd_reorder_002
      const aacTerminals = ['cmd_ens_und', 'cmd_ens_fb'];
      for (const nodeId of aacTerminals) {
        const target = edgeMap.get(`${nodeId}:1`);
        assert.strictEqual(target, 'cmd_reorder_002',
          `${nodeId} must route to cmd_reorder_002 in pass 2`);
      }
      assert.strictEqual(edgeMap.get('grd_fb_eng:1'), 'cmd_reorder_002',
        'grd_fb_eng YES must route to cmd_reorder_002 in pass 2');
    });

    test('guard chain catches orphaned stereo EAC3', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_ch YES → grd_surr_ch (check for 6+ ch surround)
      assert.strictEqual(edgeMap.get('grd_ch:1'), 'grd_surr_ch',
        '2ch check should route to surround channel check');

      // grd_surr_ch YES → grd_unwanted_dts (has surround, start unwanted audio chain)
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted_dts',
        'Files with surround should start unwanted audio chain');

      // grd_surr_ch NO → grd_has_eac3 (no surround, check for orphaned EAC3)
      assert.strictEqual(edgeMap.get('grd_surr_ch:2'), 'grd_has_eac3',
        'No surround should check for orphaned EAC3');

      // grd_has_eac3 YES → cmt_proc (stereo EAC3 without surround = needs cleanup)
      assert.strictEqual(edgeMap.get('grd_has_eac3:1'), 'cmt_proc',
        'Orphaned EAC3 should route to processing');

      // grd_has_eac3 NO → grd_unwanted_dts (no EAC3, start unwanted audio chain)
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted_dts',
        'No EAC3 should start unwanted audio chain');

      // Verify grd_has_eac3 config matches eac3
      const hasEac3 = pluginMap.get('grd_has_eac3');
      assert.ok(hasEac3, 'Missing node grd_has_eac3');
      assert.strictEqual(hasEac3.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(hasEac3.inputsDB.valuesToMatch, 'eac3');
      assert.strictEqual(hasEac3.inputsDB.condition, 'includes');
    });

    test('DoVi guard covers both MP4 and non-MP4 paths', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // MP4 path: grd_vid YES → grd_dovi
      assert.strictEqual(edgeMap.get('grd_vid:1'), 'grd_dovi',
        'MP4 HEVC should check for DoVi');
      assert.strictEqual(edgeMap.get('grd_dovi:1'), 'fl_noop',
        'DoVi detected (MP4) should skip');

      // Non-MP4 path: grd_ext NO → grd_dovi_non_mp4
      assert.strictEqual(edgeMap.get('grd_ext:2'), 'grd_dovi_non_mp4',
        'Non-MP4 should check for DoVi before processing');
      assert.strictEqual(edgeMap.get('grd_dovi_non_mp4:1'), 'fl_noop',
        'DoVi detected (non-MP4) should skip');
      assert.strictEqual(edgeMap.get('grd_dovi_non_mp4:2'), 'cmt_proc',
        'Non-DoVi non-MP4 should route to processing');

      // Verify grd_dovi_non_mp4 config matches grd_dovi
      const doviMkv = pluginMap.get('grd_dovi_non_mp4');
      assert.ok(doviMkv, 'Missing node grd_dovi_non_mp4');
      assert.strictEqual(doviMkv.inputsDB.propertyToCheck, 'codec_tag_string');
      assert.strictEqual(doviMkv.inputsDB.valuesToMatch, 'dv');
      assert.strictEqual(doviMkv.inputsDB.condition, 'includes');
    });

    test('guard chain catches unwanted audio codecs (individual single-value guards)', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Individual guard chain: each node checks one codec, YES → cmt_proc, NO → next guard
      // Tdarr v2.62.01 checkStreamProperty does NOT split comma-separated values,
      // so each codec must be its own guard node with a single valuesToMatch.
      const guardChain = [
        { id: 'grd_unwanted_dts',    value: 'dts',    condition: 'equals' },
        { id: 'grd_unwanted_dca',    value: 'dca',    condition: 'equals' },
        { id: 'grd_unwanted_mp3',    value: 'mp3',    condition: 'equals' },
        { id: 'grd_unwanted_truehd', value: 'truehd', condition: 'equals' },
        { id: 'grd_unwanted_mlp',    value: 'mlp',    condition: 'equals' },
        { id: 'grd_unwanted_flac',   value: 'flac',   condition: 'equals' },
        { id: 'grd_unwanted_vorbis', value: 'vorbis', condition: 'equals' },
        { id: 'grd_unwanted_opus',   value: 'opus',   condition: 'equals' },
        { id: 'grd_unwanted_pcm',    value: 'pcm_',   condition: 'includes' },
        { id: 'grd_unwanted_wma',    value: 'wma',    condition: 'includes' },
        { id: 'grd_unwanted_ac3',    value: 'ac3',    condition: 'equals' },
      ];

      for (let i = 0; i < guardChain.length; i++) {
        const guard = guardChain[i];
        const node = pluginMap.get(guard.id);
        assert.ok(node, `Missing node ${guard.id}`);
        assert.strictEqual(node.inputsDB.streamType, 'audio',
          `${guard.id} must check audio streams`);
        assert.strictEqual(node.inputsDB.propertyToCheck, 'codec_name',
          `${guard.id} must check codec_name`);
        assert.strictEqual(node.inputsDB.valuesToMatch, guard.value,
          `${guard.id} must match "${guard.value}"`);
        assert.strictEqual(node.inputsDB.condition, guard.condition,
          `${guard.id} must use "${guard.condition}" condition`);

        // YES → cmt_proc (has unwanted audio)
        assert.strictEqual(edgeMap.get(`${guard.id}:1`), 'cmt_proc',
          `${guard.id} YES should route to processing`);

        // NO → next guard (or cmt_optimal for last)
        const expectedNext = i < guardChain.length - 1
          ? guardChain[i + 1].id
          : 'cmt_optimal';
        assert.strictEqual(edgeMap.get(`${guard.id}:2`), expectedNext,
          `${guard.id} NO should route to ${expectedNext}`);
      }

      // Every guarded codec must be removable by the pipeline
      const rmaudio = pluginMap.get('cmd_rmaudio');
      const rmAc3 = pluginMap.get('cmd_rm_ac3');
      const rmMp3 = pluginMap.get('cmd_rm_mp3');
      assert.ok(rmaudio && rmAc3 && rmMp3, 'Missing removal nodes');
      // cmd_rm_ac3 uses codec_tag_string "ac-3" to avoid eac3 substring match —
      // map it back to codec_name "ac3" for guard cross-reference
      const tagToCodec = { 'ac-3': 'ac3' };
      const rmAc3Codecs = rmAc3.inputsDB.propertyToCheck === 'codec_tag_string'
        ? rmAc3.inputsDB.valuesToRemove.split(',').map(s => tagToCodec[s.trim()] || s.trim())
        : rmAc3.inputsDB.valuesToRemove.split(',').map(s => s.trim());
      const removableSet = new Set([
        ...rmaudio.inputsDB.valuesToRemove.split(',').map(s => s.trim()),
        ...rmAc3Codecs,
        rmMp3.inputsDB.valuesToRemove.trim(),
      ]);
      for (const guard of guardChain) {
        // For "includes" guards (pcm, wma), the guard value is a substring —
        // check that at least one removable codec contains it
        if (guard.condition === 'includes') {
          const hasMatch = [...removableSet].some(r => r.includes(guard.value));
          assert.ok(hasMatch,
            `Guard "${guard.id}" catches "${guard.value}" (includes) but no removal node strips a matching codec`);
        } else {
          assert.ok(removableSet.has(guard.value),
            `Guard "${guard.id}" catches "${guard.value}" but no removal node strips it`);
        }
      }
    });

    test('stereo AAC fallback for non-eng/non-und audio', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // cmt_audio → grd_has_eng (check for eng audio before EnsureAudioStream)
      assert.strictEqual(edgeMap.get('cmt_audio:1'), 'grd_has_eng',
        'Audio pipeline should check for eng audio first');

      // grd_has_eng YES → cmd_ens_eng (has eng, safe to create stereo)
      assert.strictEqual(edgeMap.get('grd_has_eng:1'), 'cmd_ens_eng',
        'Eng audio present should route to AAC creation');

      // grd_has_eng NO → grd_dup_und (no eng, skip eng EnsureAudioStream to prevent fallback duplicates)
      assert.strictEqual(edgeMap.get('grd_has_eng:2'), 'grd_dup_und',
        'No eng audio should skip eng AAC creation');

      // Verify grd_has_eng config
      const hasEng = pluginMap.get('grd_has_eng');
      assert.ok(hasEng, 'Missing node grd_has_eng');
      assert.strictEqual(hasEng.pluginName, 'checkStreamProperty');
      assert.strictEqual(hasEng.inputsDB.streamType, 'audio');
      assert.strictEqual(hasEng.inputsDB.propertyToCheck, 'tags.language');
      assert.strictEqual(hasEng.inputsDB.valuesToMatch, 'eng');
      assert.strictEqual(hasEng.inputsDB.condition, 'includes',
        'grd_has_eng must use "includes" condition');

      // grd_dup_und YES → cmd_ens_und directly (no redundant eng guard)
      assert.strictEqual(edgeMap.get('grd_dup_und:1'), 'cmd_ens_und',
        'und audio present should route directly to und AAC creation');
      assert.ok(!pluginMap.has('grd_dup_eng'),
        'grd_dup_eng should be removed (was redundant)');

      // grd_dup_und NO → grd_fb_eng (check if eng pass worked)
      assert.strictEqual(edgeMap.get('grd_dup_und:2'), 'grd_fb_eng',
        'No und audio should check for eng fallback');

      // grd_fb_eng YES → cmd_reorder_002 (eng pass created AAC, proceed to pass 2 reorder)
      assert.strictEqual(edgeMap.get('grd_fb_eng:1'), 'cmd_reorder_002',
        'Eng audio present should skip fallback and route to pass 2 reorder');

      // grd_fb_eng NO → cmd_ens_fb (fallback AAC creation)
      assert.strictEqual(edgeMap.get('grd_fb_eng:2'), 'cmd_ens_fb',
        'No eng/und audio should route to fallback AAC');

      // cmd_ens_fb → cmd_reorder_002 (pass 2 reorder)
      assert.strictEqual(edgeMap.get('cmd_ens_fb:1'), 'cmd_reorder_002',
        'Fallback AAC should route to pass 2 reorder');

      // Verify fallback node config
      const fb = pluginMap.get('cmd_ens_fb');
      assert.ok(fb, 'Missing node cmd_ens_fb');
      assert.strictEqual(fb.pluginName, 'ffmpegCommandEnsureAudioStream');
      assert.strictEqual(fb.inputsDB.audioEncoder, 'aac');
      assert.strictEqual(fb.inputsDB.channels, '2');

      // ── VR path: same structure ──
      // cmd_vr_rmaudio → grd_vr_has_eng (check eng before AAC creation)
      assert.strictEqual(edgeMap.get('cmd_vr_rmaudio:1'), 'grd_vr_has_eng',
        'VR: audio removal should check for eng audio first');
      assert.strictEqual(edgeMap.get('grd_vr_has_eng:1'), 'cmd_vr_aac_eng',
        'VR: eng audio present should route to AAC creation');
      assert.strictEqual(edgeMap.get('grd_vr_has_eng:2'), 'grd_vr_dup_und',
        'VR: no eng audio should skip eng AAC creation');

      // Verify grd_vr_has_eng config
      const vrHasEng = pluginMap.get('grd_vr_has_eng');
      assert.ok(vrHasEng, 'Missing node grd_vr_has_eng');
      assert.strictEqual(vrHasEng.pluginName, 'checkStreamProperty');
      assert.strictEqual(vrHasEng.inputsDB.streamType, 'audio');
      assert.strictEqual(vrHasEng.inputsDB.propertyToCheck, 'tags.language');
      assert.strictEqual(vrHasEng.inputsDB.valuesToMatch, 'eng');
      assert.strictEqual(vrHasEng.inputsDB.condition, 'includes',
        'grd_vr_has_eng must use "includes" condition');

      assert.strictEqual(edgeMap.get('grd_vr_dup_und:1'), 'cmd_vr_aac_und',
        'VR: und audio present should route directly to und AAC creation');
      assert.ok(!pluginMap.has('grd_vr_dup_eng'),
        'grd_vr_dup_eng should be removed (was redundant)');

      assert.strictEqual(edgeMap.get('grd_vr_dup_und:2'), 'grd_vr_fb_eng',
        'VR: no und audio should check for eng fallback');
      assert.strictEqual(edgeMap.get('grd_vr_fb_eng:1'), 'cmd_vr_reorder',
        'VR: eng audio present should skip fallback');
      assert.strictEqual(edgeMap.get('grd_vr_fb_eng:2'), 'cmd_vr_ens_fb',
        'VR: no eng/und audio should route to fallback AAC');
      assert.strictEqual(edgeMap.get('cmd_vr_ens_fb:1'), 'cmd_vr_reorder',
        'VR: fallback AAC should route to reorder');

      const vrFb = pluginMap.get('cmd_vr_ens_fb');
      assert.ok(vrFb, 'Missing node cmd_vr_ens_fb');
      assert.strictEqual(vrFb.pluginName, 'ffmpegCommandEnsureAudioStream');
      assert.strictEqual(vrFb.inputsDB.audioEncoder, 'aac');
      assert.strictEqual(vrFb.inputsDB.channels, '2');

      // VR removal node should also strip PCM
      const vrRmaudio = pluginMap.get('cmd_vr_rmaudio');
      assert.ok(vrRmaudio, 'Missing node cmd_vr_rmaudio');
      const vrRemovableSet = new Set(
        vrRmaudio.inputsDB.valuesToRemove.split(',').map(s => s.trim())
      );
      for (const codec of ['pcm_s16le', 'pcm_s24le']) {
        assert.ok(vrRemovableSet.has(codec),
          `cmd_vr_rmaudio should strip ${codec}`);
      }
    });

    test('image removal nodes strip bin_data streams', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const rmimages = pluginMap.get('cmd_rmimages');
      assert.ok(rmimages, 'Missing node cmd_rmimages');
      assert.ok(rmimages.inputsDB.valuesToRemove.includes('bin_data'),
        'cmd_rmimages must strip bin_data streams');

      const vrRmimages = pluginMap.get('cmd_vr_rmimages');
      assert.ok(vrRmimages, 'Missing node cmd_vr_rmimages');
      assert.ok(vrRmimages.inputsDB.valuesToRemove.includes('bin_data'),
        'cmd_vr_rmimages must strip bin_data streams');
    });

    test('DTS is stripped by audio removal nodes', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const rmaudio = pluginMap.get('cmd_rmaudio');
      assert.ok(rmaudio, 'Missing node cmd_rmaudio');
      assert.ok(rmaudio.inputsDB.valuesToRemove.includes('dts'),
        'cmd_rmaudio must remove dts (ffprobe codec_name variant)');

      // Pass 2 removal: AC3 uses codec_tag_string "ac-3" (avoids eac3 substring match),
      // MP3 uses codec_name "mp3" (no ambiguity). Both use "includes" — the only working
      // condition in ffmpegCommandRemoveStreamByProperty (Tdarr v2.62.01 has no "equals").
      const rmAc3 = pluginMap.get('cmd_rm_ac3');
      const rmMp3 = pluginMap.get('cmd_rm_mp3');
      assert.ok(rmAc3, 'Missing node cmd_rm_ac3');
      assert.ok(rmMp3, 'Missing node cmd_rm_mp3');
      assert.strictEqual(rmAc3.inputsDB.propertyToCheck, 'codec_tag_string',
        'cmd_rm_ac3 must use codec_tag_string to distinguish ac-3 from ec-3');
      assert.strictEqual(rmAc3.inputsDB.valuesToRemove, 'ac-3',
        'cmd_rm_ac3 must target ac-3 (MP4 tag for AC3)');
      assert.strictEqual(rmAc3.inputsDB.condition, 'includes',
        'cmd_rm_ac3 must use includes (only working condition in RemoveStreamByProperty)');
      assert.strictEqual(rmMp3.inputsDB.valuesToRemove, 'mp3',
        'cmd_rm_mp3 must target mp3');
      assert.strictEqual(rmMp3.inputsDB.condition, 'includes',
        'cmd_rm_mp3 must use includes (only working condition in RemoveStreamByProperty)');
    });

    test('hvc1 tag set in both normal and VR paths', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Normal path: cmd_tags in pass 1 sets hvc1 + profile
      const tags = pluginMap.get('cmd_tags');
      assert.ok(tags, 'Missing node cmd_tags');
      assert.ok(tags.inputsDB.outputArguments.includes('-tag:v hvc1'),
        'cmd_tags must include -tag:v hvc1');

      // Normal path: faststart moved to pass 2 (cmd_faststart2)
      const faststart2 = pluginMap.get('cmd_faststart2');
      assert.ok(faststart2, 'Missing node cmd_faststart2');
      assert.ok(faststart2.inputsDB.outputArguments.includes('+faststart'),
        'cmd_faststart2 must include +faststart');
      assert.ok(faststart2.inputsDB.outputArguments.includes('-tag:v hvc1'),
        'cmd_faststart2 must include -tag:v hvc1 (pass 2 remux may reset tag)');

      // Faststart must NOT be in pass 1 (moved to pass 2 for probe reliability)
      assert.ok(!tags.inputsDB.outputArguments.includes('+faststart'),
        'cmd_tags must NOT include +faststart (moved to pass 2)');

      // VR pass 1 tags must NOT include faststart (moved to VR pass 2)
      const vrTags = pluginMap.get('cmd_vr_tags');
      assert.ok(vrTags, 'Missing node cmd_vr_tags');
      assert.ok(!vrTags.inputsDB.outputArguments.includes('+faststart'),
        'cmd_vr_tags must NOT include +faststart (moved to VR pass 2)');

      // VR path remux (pass 2)
      const vrFaststart2 = pluginMap.get('cmd_vr_faststart2');
      assert.ok(vrFaststart2, 'Missing node cmd_vr_faststart2');
      assert.ok(vrFaststart2.inputsDB.outputArguments.includes('+faststart'),
        'cmd_vr_faststart2 must include +faststart');
      assert.ok(vrFaststart2.inputsDB.outputArguments.includes('-tag:v hvc1'),
        'cmd_vr_faststart2 must include -tag:v hvc1 to prevent FFmpeg defaulting to hev1');
    });

    test('attachments are stripped in first pipeline', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const node = pluginMap.get('cmd_rmattach');
      assert.ok(node, 'Missing node cmd_rmattach');
      assert.strictEqual(node.inputsDB.propertyToCheck, 'codec_type');
      assert.strictEqual(node.inputsDB.valuesToRemove, 'attachment');
      assert.strictEqual(node.inputsDB.condition, 'includes',
        'cmd_rmattach must use "includes" (not "equals") — equals fails on attachment-type streams');

      // cmd_rmimages → cmd_rmattach → cmt_nvenc
      assert.strictEqual(edgeMap.get('cmd_rmimages:1'), 'cmd_rmattach');
      assert.strictEqual(edgeMap.get('cmd_rmattach:1'), 'cmt_nvenc');
    });

    test('encode tag nodes include -profile:v main10 for 10-bit output', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Normal path (shared by NVENC and SW encoders)
      const cmdTags = pluginMap.get('cmd_tags');
      assert.ok(cmdTags, 'Missing node cmd_tags');
      assert.ok(cmdTags.inputsDB.outputArguments.includes('-profile:v main10'),
        'cmd_tags must include -profile:v main10 for 10-bit HEVC output');

      // VR path
      const vrTags = pluginMap.get('cmd_vr_tags');
      assert.ok(vrTags, 'Missing node cmd_vr_tags');
      assert.ok(vrTags.inputsDB.outputArguments.includes('-profile:v main10'),
        'cmd_vr_tags must include -profile:v main10 for 10-bit HEVC output');
    });

    test('VR encoder does not force re-encoding of existing HEVC', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));
      const vrHevc = pluginMap.get('cmd_vr_hevc');
      assert.ok(vrHevc, 'Missing node cmd_vr_hevc');
      assert.strictEqual(vrHevc.inputsDB.forceEncoding, 'false',
        'cmd_vr_hevc forceEncoding must be "false" — 8K HEVC files exceed T400 VRAM and only need retagging');
    });

    test('VR retag shortcut guards and pipeline wiring', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // chk_vr YES → VR retag guard chain (not directly to cmt_vr)
      assert.strictEqual(edgeMap.get('chk_vr:1'), 'grd_vr_ismp4',
        'chk_vr YES must route to VR retag guard chain');

      // Guard chain routing
      assert.strictEqual(edgeMap.get('grd_vr_ismp4:1'), 'grd_vr_ishevc');
      assert.strictEqual(edgeMap.get('grd_vr_ismp4:2'), 'cmt_vr');
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:1'), 'grd_vr_nw_dts',
        'HEVC VR should start unwanted audio chain');
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:2'), 'cmt_vr');

      // VR unwanted audio guard chain (individual single-value guards)
      const vrGuardChain = [
        'grd_vr_nw_dts', 'grd_vr_nw_dca', 'grd_vr_nw_mp3',
        'grd_vr_nw_truehd', 'grd_vr_nw_mlp', 'grd_vr_nw_flac',
        'grd_vr_nw_vorbis', 'grd_vr_nw_opus', 'grd_vr_nw_pcm',
        'grd_vr_nw_wma', 'grd_vr_nowanted_ac3',
      ];
      for (let i = 0; i < vrGuardChain.length; i++) {
        const id = vrGuardChain[i];
        assert.strictEqual(edgeMap.get(`${id}:1`), 'cmt_vr',
          `${id} YES → full VR pipeline`);
        const expectedNext = i < vrGuardChain.length - 1
          ? vrGuardChain[i + 1]
          : 'grd_vr_hasaac';
        assert.strictEqual(edgeMap.get(`${id}:2`), expectedNext,
          `${id} NO → ${expectedNext}`);
      }
      assert.strictEqual(edgeMap.get('grd_vr_hasaac:1'), 'cmt_vr_retag',
        'has AAC → retag shortcut');
      assert.strictEqual(edgeMap.get('grd_vr_hasaac:2'), 'cmt_vr',
        'no AAC → full VR pipeline');

      // Retag pipeline wiring
      assert.strictEqual(edgeMap.get('cmt_vr_retag:1'), 'ffs_vr_retag');
      assert.strictEqual(edgeMap.get('ffs_vr_retag:1'), 'cmd_vr_retag_mp4');
      assert.strictEqual(edgeMap.get('cmd_vr_retag_mp4:1'), 'cmd_vr_retag_rmdata');
      assert.strictEqual(edgeMap.get('cmd_vr_retag_rmdata:1'), 'cmd_vr_retag_enc');
      assert.strictEqual(edgeMap.get('cmd_vr_retag_enc:1'), 'cmd_vr_retag_tags');
      assert.strictEqual(edgeMap.get('cmd_vr_retag_tags:1'), 'ffe_vr_retag');
      assert.strictEqual(edgeMap.get('ffe_vr_retag:1'), 'fl_vr_size',
        'retag output joins existing VR validation chain');

      // Retag encoder must NOT use hardware
      const retagEnc = pluginMap.get('cmd_vr_retag_enc');
      assert.ok(retagEnc, 'Missing node cmd_vr_retag_enc');
      assert.strictEqual(retagEnc.inputsDB.forceEncoding, 'false');
      assert.strictEqual(retagEnc.inputsDB.hardwareEncoding, 'false',
        'VR retag must not use hardware encoding');
      assert.strictEqual(retagEnc.inputsDB.hardwareDecoding, 'false',
        'VR retag must not use hardware decoding');

      // Retag container must preserve spherical metadata
      const retagMp4 = pluginMap.get('cmd_vr_retag_mp4');
      assert.ok(retagMp4, 'Missing node cmd_vr_retag_mp4');
      assert.strictEqual(retagMp4.inputsDB.forceConform, 'false',
        'VR retag must preserve spherical metadata (forceConform=false)');

      // Retag pipeline must strip data streams (tmcd etc. cause MP4 muxer failure)
      const retagRmdata = pluginMap.get('cmd_vr_retag_rmdata');
      assert.ok(retagRmdata, 'Missing node cmd_vr_retag_rmdata');
      assert.strictEqual(retagRmdata.pluginName, 'ffmpegCommandRemoveDataStreams',
        'VR retag must remove data streams to avoid MP4 muxer errors');

      // Retag tags must include hvc1, faststart, and audio copy
      const retagTags = pluginMap.get('cmd_vr_retag_tags');
      assert.ok(retagTags, 'Missing node cmd_vr_retag_tags');
      assert.ok(retagTags.inputsDB.outputArguments.includes('-tag:v hvc1'),
        'VR retag must set hvc1 tag');
      assert.ok(retagTags.inputsDB.outputArguments.includes('+faststart'),
        'VR retag must set faststart');
      assert.ok(retagTags.inputsDB.outputArguments.includes('-c:a copy'),
        'VR retag must copy audio (no re-encode)');
    });

    test('ffmpegCommandRemoveStreamByProperty nodes use correct condition', () => {
      // All RemoveStreamByProperty nodes must use "includes" — it's the only working
      // condition in Tdarr v2.62.01 (unrecognized conditions like "equals" fall through
      // to not_includes, which inverts the logic and removes wrong streams).
      // AC3 removal uses codec_tag_string "ac-3" to avoid the eac3 substring match.
      const removeNodes = flow.flowPlugins.filter(
        (p) => p.pluginName === 'ffmpegCommandRemoveStreamByProperty'
      );
      for (const node of removeNodes) {
        assert.strictEqual(node.inputsDB.condition, 'includes',
          `${node.id} must use "includes" — the only working condition in RemoveStreamByProperty`);
      }
    });

    test('timestamp normalization applied to all FFmpeg pipelines', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));
      const FLAG = '-avoid_negative_ts make_zero';

      // Normal path: cmd_loglevel injects outputArguments into pass 1
      const cmdLoglevel = pluginMap.get('cmd_loglevel');
      assert.ok(cmdLoglevel, 'cmd_loglevel node must exist');
      assert.ok(cmdLoglevel.inputsDB.outputArguments.includes(FLAG),
        `cmd_loglevel must include "${FLAG}" to prevent corrupt MP4 from non-monotonic DTS`);

      // VR full path: cmd_vr_loglevel injects outputArguments into VR pipeline
      const cmdVrLoglevel = pluginMap.get('cmd_vr_loglevel');
      assert.ok(cmdVrLoglevel, 'cmd_vr_loglevel node must exist');
      assert.ok(cmdVrLoglevel.inputsDB.outputArguments.includes(FLAG),
        `cmd_vr_loglevel must include "${FLAG}" to prevent corrupt MP4 from non-monotonic DTS`);

      // VR retag shortcut: cmd_vr_retag_tags carries output arguments for the retag pipeline
      const cmdVrRetagTags = pluginMap.get('cmd_vr_retag_tags');
      assert.ok(cmdVrRetagTags, 'cmd_vr_retag_tags node must exist');
      assert.ok(cmdVrRetagTags.inputsDB.outputArguments.includes(FLAG),
        `cmd_vr_retag_tags must include "${FLAG}" to prevent corrupt MP4 from non-monotonic DTS`);
    });

    test('onFlowError does not replace original file', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Collect ALL outgoing edges from err_on (not just one via Map)
      const errEdges = flow.flowEdges.filter((e) => e.source === 'err_on');
      assert.ok(errEdges.length > 0, 'onFlowError must have at least one outgoing edge');

      for (const edge of errEdges) {
        const targetNode = pluginMap.get(edge.target);
        assert.ok(targetNode, `Missing target node ${edge.target}`);
        assert.notStrictEqual(targetNode.pluginName, 'replaceOriginalFile',
          `onFlowError edge ${edge.id} must NOT route to replaceOriginalFile — a failed transcode would overwrite the original with a broken working file`);

        // Each target should be a safe terminal (comment node with no outgoing edges)
        assert.strictEqual(targetNode.pluginName, 'comment',
          `onFlowError target ${edge.target} should be a comment node to preserve the original file`);
        const outgoing = flow.flowEdges.filter((e) => e.source === edge.target);
        assert.strictEqual(outgoing.length, 0,
          `onFlowError terminal ${edge.target} must have no outgoing edges`);
      }
    });
  });
}
