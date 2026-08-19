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
      const VALID_REPOS = new Set(['Community', 'Local']);
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
      for (const enc of ['cmd_hevc_sd', 'cmd_hevc_1080', 'cmd_hevc_4k', 'cmd_hevc_force']) {
        assert.strictEqual(edgeMap.get(`${enc}:1`), 'chk_br_vlow', `${enc} should route to chk_br_vlow`);
      }

      // chk_br_vlow → cmd_cap_vlow (yes) / chk_br_low (no)
      assert.strictEqual(edgeMap.get('chk_br_vlow:1'), 'cmd_cap_vlow');
      assert.strictEqual(edgeMap.get('chk_br_vlow:2'), 'chk_br_low');

      // chk_br_low → cmd_cap_low (yes) / chk_br_mid (no)
      assert.strictEqual(edgeMap.get('chk_br_low:1'), 'cmd_cap_low');
      assert.strictEqual(edgeMap.get('chk_br_low:2'), 'chk_br_mid');

      // chk_br_mid → cmd_cap_mid (yes) / cmd_cap_high (no = above 10 Mbps)
      assert.strictEqual(edgeMap.get('chk_br_mid:1'), 'cmd_cap_mid');
      assert.strictEqual(edgeMap.get('chk_br_mid:2'), 'cmd_cap_high');

      // All caps → cmt_tags
      for (const cap of ['cmd_cap_vlow', 'cmd_cap_low', 'cmd_cap_mid', 'cmd_cap_high']) {
        assert.strictEqual(edgeMap.get(`${cap}:1`), 'cmt_tags', `${cap} should route to cmt_tags`);
      }

      // SW encoder bypasses caps → cmt_tags
      assert.strictEqual(edgeMap.get('cmd_hevc_sw:1'), 'cmt_tags');
    });

    test('MKV force-encode gate is wired correctly', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_is_mkv exists and is a checkFileExtension node
      const grdMkv = pluginMap.get('grd_is_mkv');
      assert.ok(grdMkv, 'Missing node grd_is_mkv');
      assert.strictEqual(grdMkv.pluginName, 'checkFileExtension');
      assert.strictEqual(grdMkv.inputsDB.extensions, 'mkv');

      // grd_mkv_hevc exists and is a checkVideoCodec node
      const grdMkvHevc = pluginMap.get('grd_mkv_hevc');
      assert.ok(grdMkvHevc, 'Missing node grd_mkv_hevc');
      assert.strictEqual(grdMkvHevc.pluginName, 'checkVideoCodec');
      assert.strictEqual(grdMkvHevc.inputsDB.codec, 'hevc');

      // cmd_hevc_force exists with forceEncoding true
      const forceEnc = pluginMap.get('cmd_hevc_force');
      assert.ok(forceEnc, 'Missing node cmd_hevc_force');
      assert.strictEqual(forceEnc.pluginName, 'ffmpegCommandSetVideoEncoder');
      assert.strictEqual(forceEnc.inputsDB.forceEncoding, 'true');
      assert.strictEqual(forceEnc.inputsDB.outputCodec, 'hevc');

      // cmt_nvenc → grd_vc1 → (NO) → grd_is_mkv
      assert.strictEqual(edgeMap.get('cmt_nvenc:1'), 'grd_vc1',
        'cmt_nvenc should route to the VC-1/WMV guard first');
      assert.strictEqual(edgeMap.get('grd_vc1:2'), 'grd_is_mkv',
        'non-VC-1 sources should continue to the MKV gate');

      // grd_is_mkv YES → grd_mkv_hevc (check if already HEVC)
      assert.strictEqual(edgeMap.get('grd_is_mkv:1'), 'grd_mkv_hevc',
        'MKV files should route to HEVC check');

      // grd_is_mkv NO → grd_av1 (existing path)
      assert.strictEqual(edgeMap.get('grd_is_mkv:2'), 'grd_av1',
        'Non-MKV files should route to existing AV1 check');

      // grd_mkv_hevc YES → grd_av1 (stream-copy path)
      assert.strictEqual(edgeMap.get('grd_mkv_hevc:1'), 'grd_av1',
        'MKV+HEVC should stream-copy via normal path');

      // grd_mkv_hevc NO → cmd_hevc_force (must transcode)
      assert.strictEqual(edgeMap.get('grd_mkv_hevc:2'), 'cmd_hevc_force',
        'MKV+non-HEVC should force encode');

      // cmd_hevc_force → chk_br_vlow (rejoin bitrate cap chain)
      assert.strictEqual(edgeMap.get('cmd_hevc_force:1'), 'chk_br_vlow',
        'Force encoder should route to bitrate cap chain');
    });

    test('bitrate cap CQ values are not below encoder QP', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const caps = [
        { id: 'cmd_cap_vlow', minCQ: 28 },
        { id: 'cmd_cap_low', minCQ: 28 },
        { id: 'cmd_cap_mid', minCQ: 24 },
        { id: 'cmd_cap_high', minCQ: 24 },
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

      // Non-surround path skips EAC3 removal (preserves audio for pass 2 AAC creation)
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:2'), 'cmt_reorder',
        'Non-surround channel path should skip to reorder (no EAC3 removal)');

      // Pass 1 ending: reorder → strip MP4-incompatible audio → execute
      assert.strictEqual(edgeMap.get('cmd_reorder:1'), 'cmt_rmmux',
        'Stream reorder should route to mux-incompatible removal');
      assert.ok(pluginMap.has('cmt_rmmux'), 'cmt_rmmux comment node must exist');
      assert.ok(pluginMap.has('cmd_rmmux'), 'cmd_rmmux removal node must exist');
      assert.strictEqual(pluginMap.get('cmd_rmmux').pluginName, 'ffmpegCommandRemoveStreamByProperty',
        'cmd_rmmux must be a RemoveStreamByProperty plugin');
      assert.strictEqual(edgeMap.get('cmt_rmmux:1'), 'cmd_rmmux',
        'Mux-incompatible comment should route to removal');
      assert.strictEqual(edgeMap.get('cmd_rmmux:1'), 'cmt_exec',
        'Mux-incompatible removal should route to execute comment');

      // cmd_rmmux strips codecs that cannot be muxed into MP4 (vorbis, opus, wma, adpcm)
      const rmmuxNode = pluginMap.get('cmd_rmmux');
      assert.strictEqual(rmmuxNode.inputsDB.condition, 'includes',
        'cmd_rmmux must use "includes" condition');
      for (const codec of ['vorbis', 'opus', 'wma', 'adpcm']) {
        assert.ok(rmmuxNode.inputsDB.valuesToRemove.includes(codec),
          `cmd_rmmux must strip ${codec} (MP4-incompatible)`);
      }

      // Old second-pass nodes from pre-#49 must still be gone
      assert.ok(!pluginMap.has('ffs_reorder'),
        'ffs_reorder must be removed — old second pass');
      assert.ok(!pluginMap.has('ffe_reorder'),
        'ffe_reorder must be removed — old second pass');
      assert.ok(!pluginMap.has('cmt_reorder2'),
        'cmt_reorder2 must be removed — old second pass');

      // fail_toobig must not exist — oversized files route to manual review
      assert.ok(!pluginMap.has('fail_toobig'),
        'fail_toobig must be removed — oversized files route to fl_manual_review');

      // Post-encode chain:
      //   ffe_001 → chk_health_002 →
      //   AAC pass:     ffs_002 → [AAC section] → ffe_aac →
      //   Cleanup pass: ffs_003 → cmd_rmdata_003 → grd_p3_has_aac
      //     YES → cmt_rmaudio → cmd_rmaudio → cmd_rm_ac3 → cmd_rm_mp3 → cmd_reorder_002 → cmd_faststart2 → ffe_002
      //     NO  → cmd_reorder_002 → cmd_faststart2 → ffe_002
      //   → cmt_size
      assert.strictEqual(edgeMap.get('ffe_001:1'), 'chk_health_002',
        'ffe_001 should route to health check');
      assert.strictEqual(edgeMap.get('chk_health_002:1'), 'ffs_002',
        'Health check should route to AAC pass start');
      // runHealthCheck throws on failure — pass 2 failure is caught by onFlowError,
      // which routes through the retry gate. The handle-2 edge remains as
      // defense-in-depth in case the community plugin is ever updated to emit it.
      assert.strictEqual(edgeMap.get('chk_health_002:2'), 'chk_retried',
        'Health check handle 2 must route to retry gate (defense-in-depth)');
      assert.ok(pluginMap.has('chk_retried'),
        'chk_retried plugin node must exist');
      assert.strictEqual(pluginMap.get('chk_retried').pluginName, 'checkFlowVariable',
        'chk_retried must be a checkFlowVariable node');

      // onFlowError routes through the retry gate so runHealthCheck throws
      // (the real failure mode) trigger the retry pipeline.
      assert.strictEqual(edgeMap.get('err_on:1'), 'chk_retried',
        'onFlowError must route to retry gate so thrown health check failures trigger retry');

      // Retry gate: already retried -> error terminal, first failure -> retry pipeline
      assert.strictEqual(edgeMap.get('chk_retried:1'), 'cmt_err_end',
        'Already retried must route to error terminal (preserve original, no loop)');
      assert.strictEqual(edgeMap.get('chk_retried:2'), 'set_retry',
        'First failure must route to retry pipeline');
      assert.ok(!pluginMap.has('fail_health2'),
        'fail_health2 must be removed — second-failure path goes through cmt_err_end');

      // Retry pipeline must reset flowFailed before running, otherwise the
      // engine keeps routing straight back to onFlowError.
      assert.strictEqual(edgeMap.get('set_retry:1'), 'rst_original',
        'set_retry must route to reset-original');
      assert.strictEqual(edgeMap.get('rst_original:1'), 'rst_error',
        'rst_original must route to rst_error to clear flowFailed before retry');
      assert.strictEqual(edgeMap.get('rst_error:1'), 'ffs_retry',
        'rst_error must route to ffs_retry (begin retry encode)');
      assert.ok(pluginMap.has('rst_error'),
        'rst_error plugin node must exist');
      assert.strictEqual(pluginMap.get('rst_error').pluginName, 'resetFlowError',
        'rst_error must be a resetFlowError node');

      // Retry pipeline ends at health check -> pass 2 (success path only).
      // If the retry's health check throws again, onFlowError fires and the
      // retry gate sees retry_encode=true, routing to cmt_err_end. No infinite loop.
      assert.strictEqual(edgeMap.get('chk_health_retry:1'), 'ffs_002',
        'Retry health check pass must route to AAC pass');
      assert.strictEqual(edgeMap.get('ffs_002:1'), 'cmt_audio',
        'AAC pass start should route to AAC stereo section');

      // ffe_aac executes AAC creation, ffs_003 rescans so clones have real codec_name
      assert.ok(pluginMap.has('ffe_aac'), 'ffe_aac plugin node must exist');
      assert.strictEqual(pluginMap.get('ffe_aac').pluginName, 'ffmpegCommandExecute',
        'ffe_aac must be an ffmpegCommandExecute node');
      assert.ok(pluginMap.has('ffs_003'), 'ffs_003 plugin node must exist');
      assert.strictEqual(pluginMap.get('ffs_003').pluginName, 'ffmpegCommandStart',
        'ffs_003 must be an ffmpegCommandStart node');
      assert.strictEqual(edgeMap.get('ffe_aac:1'), 'ffs_003',
        'AAC execute should route to cleanup pass start');
      assert.strictEqual(edgeMap.get('ffs_003:1'), 'cmd_rmdata_003',
        'Cleanup pass start should route to data stream removal');
      assert.ok(pluginMap.has('cmd_rmdata_003'), 'cmd_rmdata_003 plugin node must exist');
      assert.strictEqual(pluginMap.get('cmd_rmdata_003').pluginName, 'ffmpegCommandRemoveDataStreams',
        'cmd_rmdata_003 must be a RemoveDataStreams plugin');
      // Cleanup pass AAC guard: skip audio removal if no AAC was created
      assert.strictEqual(edgeMap.get('cmd_rmdata_003:1'), 'grd_p3_has_aac',
        'Data stream removal should route to AAC guard');
      assert.ok(pluginMap.has('grd_p3_has_aac'), 'grd_p3_has_aac must exist');
      assert.strictEqual(pluginMap.get('grd_p3_has_aac').pluginName, 'checkStreamProperty',
        'grd_p3_has_aac must be a checkStreamProperty plugin');
      assert.strictEqual(edgeMap.get('grd_p3_has_aac:1'), 'cmt_rmaudio',
        'AAC guard YES should route to audio removal');
      assert.strictEqual(edgeMap.get('grd_p3_has_aac:2'), 'cmd_reorder_002',
        'AAC guard NO should skip removal and route to reorder');
      assert.strictEqual(edgeMap.get('cmt_rmaudio:1'), 'cmd_rmaudio',
        'Audio removal comment should route to audio removal');
      assert.strictEqual(edgeMap.get('cmd_rmaudio:1'), 'cmd_rm_ac3',
        'Audio removal should route to AC3 removal');
      assert.strictEqual(edgeMap.get('cmd_rm_ac3:1'), 'cmd_rm_mp3',
        'AC3 removal should route to MP3 removal');
      assert.strictEqual(edgeMap.get('cmd_rm_mp3:1'), 'cmd_reorder_002',
        'MP3 removal should route to reorder');
      assert.strictEqual(edgeMap.get('cmd_reorder_002:1'), 'cmd_faststart2',
        'Stream reorder should route to faststart');
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
      // cmd_rm_eac3 removed — orphaned EAC3 preserved for pass 2 AAC source
      assert.ok(!pluginMap.has('cmd_rm_eac3'),
        'cmd_rm_eac3 must be removed (audio preserved for pass 2)');

      // AAC stereo section must be wired into pass 2
      assert.strictEqual(edgeMap.get('ffs_002:1'), 'cmt_audio',
        'ffs_002 must route to cmt_audio (AAC stereo section)');

      // AAC section terminals must route to ffe_aac (execute AAC pass before cleanup)
      const aacTerminals = ['cmd_ens_und', 'cmd_ens_fb'];
      for (const nodeId of aacTerminals) {
        const target = edgeMap.get(`${nodeId}:1`);
        assert.strictEqual(target, 'ffe_aac',
          `${nodeId} must route to ffe_aac to execute AAC creation before cleanup`);
      }
      assert.strictEqual(edgeMap.get('grd_fb_eng:1'), 'ffe_aac',
        'grd_fb_eng YES must route to ffe_aac (AAC already created by eng pass)');
    });

    test('guard chain catches orphaned stereo EAC3', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_ch YES → grd_surr_ch (check for 6+ ch surround)
      assert.strictEqual(edgeMap.get('grd_ch:1'), 'grd_surr_ch',
        '2ch check should route to surround channel check');

      // grd_surr_ch YES → grd_unwanted_exact (has surround, start unwanted audio chain)
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted_exact',
        'Files with surround should start unwanted audio chain');

      // grd_surr_ch NO → grd_has_eac3 (no surround, check for orphaned EAC3)
      assert.strictEqual(edgeMap.get('grd_surr_ch:2'), 'grd_has_eac3',
        'No surround should check for orphaned EAC3');

      // grd_has_eac3 YES → cmt_proc (stereo EAC3 without surround = needs cleanup)
      assert.strictEqual(edgeMap.get('grd_has_eac3:1'), 'cmt_proc',
        'Orphaned EAC3 should route to processing');

      // grd_has_eac3 NO → grd_unwanted_exact (no EAC3, start unwanted audio chain)
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted_exact',
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

    test('guard chain catches unwanted audio codecs (multi-value guards)', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Two-node replacement for the old 11-node single-value chain.
      // grd_unwanted_exact: exact-match codecs (codec_name equals any of the listed values)
      const exactNode = pluginMap.get('grd_unwanted_exact');
      assert.ok(exactNode, 'Missing node grd_unwanted_exact');
      assert.strictEqual(exactNode.pluginName, 'checkStreamPropertyMultiValue',
        'grd_unwanted_exact must use checkStreamPropertyMultiValue');
      assert.strictEqual(exactNode.sourceRepo, 'Local',
        'grd_unwanted_exact must have sourceRepo "Local"');
      assert.strictEqual(exactNode.inputsDB.streamType, 'audio',
        'grd_unwanted_exact must check audio streams');
      assert.strictEqual(exactNode.inputsDB.propertyToCheck, 'codec_name',
        'grd_unwanted_exact must check codec_name');
      assert.strictEqual(exactNode.inputsDB.condition, 'equals',
        'grd_unwanted_exact must use "equals" condition');
      const exactValues = exactNode.inputsDB.valuesToMatch.split(',').map(s => s.trim());
      for (const codec of ['dts', 'dca', 'mp3', 'truehd', 'mlp', 'flac', 'vorbis', 'opus', 'ac3']) {
        assert.ok(exactValues.includes(codec),
          `grd_unwanted_exact must match "${codec}"`);
      }

      // grd_unwanted_partial: partial-match codecs (codec_name includes any of the listed values)
      const partialNode = pluginMap.get('grd_unwanted_partial');
      assert.ok(partialNode, 'Missing node grd_unwanted_partial');
      assert.strictEqual(partialNode.pluginName, 'checkStreamPropertyMultiValue',
        'grd_unwanted_partial must use checkStreamPropertyMultiValue');
      assert.strictEqual(partialNode.sourceRepo, 'Local',
        'grd_unwanted_partial must have sourceRepo "Local"');
      assert.strictEqual(partialNode.inputsDB.streamType, 'audio',
        'grd_unwanted_partial must check audio streams');
      assert.strictEqual(partialNode.inputsDB.propertyToCheck, 'codec_name',
        'grd_unwanted_partial must check codec_name');
      assert.strictEqual(partialNode.inputsDB.condition, 'includes',
        'grd_unwanted_partial must use "includes" condition');
      for (const codec of ['pcm', 'wma']) {
        assert.ok(partialNode.inputsDB.valuesToMatch.includes(codec),
          `grd_unwanted_partial must match "${codec}"`);
      }

      // Edge wiring
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted_exact',
        'grd_surr_ch YES should route to grd_unwanted_exact');
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted_exact',
        'grd_has_eac3 NO should route to grd_unwanted_exact');
      assert.strictEqual(edgeMap.get('grd_unwanted_exact:1'), 'cmt_proc',
        'grd_unwanted_exact YES should route to processing');
      assert.strictEqual(edgeMap.get('grd_unwanted_exact:2'), 'grd_unwanted_partial',
        'grd_unwanted_exact NO should route to grd_unwanted_partial');
      assert.strictEqual(edgeMap.get('grd_unwanted_partial:1'), 'cmt_proc',
        'grd_unwanted_partial YES should route to processing');
      assert.strictEqual(edgeMap.get('grd_unwanted_partial:2'), 'cmt_optimal',
        'grd_unwanted_partial NO should route to cmt_optimal');

      // All 11 old single-value guard nodes must no longer exist
      const oldGuards = [
        'grd_unwanted_dts', 'grd_unwanted_dca', 'grd_unwanted_mp3',
        'grd_unwanted_truehd', 'grd_unwanted_mlp', 'grd_unwanted_flac',
        'grd_unwanted_vorbis', 'grd_unwanted_opus', 'grd_unwanted_pcm',
        'grd_unwanted_wma', 'grd_unwanted_ac3',
      ];
      for (const id of oldGuards) {
        assert.ok(!pluginMap.has(id), `Old guard node "${id}" must be removed`);
      }

      // Every guarded codec must be removable by the pipeline
      const rmaudio = pluginMap.get('cmd_rmaudio');
      const rmmux = pluginMap.get('cmd_rmmux');
      const rmAc3 = pluginMap.get('cmd_rm_ac3');
      const rmMp3 = pluginMap.get('cmd_rm_mp3');
      assert.ok(rmaudio && rmmux && rmAc3 && rmMp3, 'Missing removal nodes');
      // cmd_rm_ac3 uses codec_tag_string "ac-3" to avoid eac3 substring match —
      // map it back to codec_name "ac3" for guard cross-reference
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
      // Exact-match guard codecs must each appear in the removable set
      const exactCodecs = exactNode.inputsDB.valuesToMatch.split(',').map(s => s.trim());
      for (const codec of exactCodecs) {
        assert.ok(removableSet.has(codec),
          `Guard grd_unwanted_exact catches "${codec}" but no removal node strips it`);
      }
      // Partial-match guard values are substrings — at least one removable codec must contain each
      const partialValues = partialNode.inputsDB.valuesToMatch.split(',').map(s => s.trim());
      for (const val of partialValues) {
        const hasMatch = [...removableSet].some(r => r.includes(val));
        assert.ok(hasMatch,
          `Guard grd_unwanted_partial catches "${val}" (includes) but no removal node strips a matching codec`);
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

      // grd_fb_eng YES → ffe_aac (eng pass created AAC, execute before cleanup)
      assert.strictEqual(edgeMap.get('grd_fb_eng:1'), 'ffe_aac',
        'Eng audio present should skip fallback and route to AAC execute');

      // grd_fb_eng NO → cmd_ens_fb (fallback AAC creation)
      assert.strictEqual(edgeMap.get('grd_fb_eng:2'), 'cmd_ens_fb',
        'No eng/und audio should route to fallback AAC');

      // cmd_ens_fb → ffe_aac (execute AAC pass before cleanup)
      assert.strictEqual(edgeMap.get('cmd_ens_fb:1'), 'ffe_aac',
        'Fallback AAC should route to AAC execute before cleanup');

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
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:1'), 'grd_vr_unwanted_exact',
        'HEVC VR should start unwanted audio chain');
      assert.strictEqual(edgeMap.get('grd_vr_ishevc:2'), 'cmt_vr');

      // VR unwanted audio guard chain (2-node multi-value replacement)
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_exact:1'), 'cmt_vr',
        'grd_vr_unwanted_exact YES → full VR pipeline');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_exact:2'), 'grd_vr_unwanted_partial',
        'grd_vr_unwanted_exact NO → grd_vr_unwanted_partial');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_partial:1'), 'cmt_vr',
        'grd_vr_unwanted_partial YES → full VR pipeline');
      assert.strictEqual(edgeMap.get('grd_vr_unwanted_partial:2'), 'grd_vr_hasaac',
        'grd_vr_unwanted_partial NO → grd_vr_hasaac');

      // All 11 old VR single-value guard nodes must no longer exist
      const oldVrGuards = [
        'grd_vr_nw_dts', 'grd_vr_nw_dca', 'grd_vr_nw_mp3',
        'grd_vr_nw_truehd', 'grd_vr_nw_mlp', 'grd_vr_nw_flac',
        'grd_vr_nw_vorbis', 'grd_vr_nw_opus', 'grd_vr_nw_pcm',
        'grd_vr_nw_wma', 'grd_vr_nowanted_ac3',
      ];
      for (const id of oldVrGuards) {
        assert.ok(!pluginMap.has(id), `Old VR guard node "${id}" must be removed`);
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

    test('mux-incompatible-only audio converts instead of dead-ending', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // chk_vr NO → grd_has_muxincompat (not directly to ffs_001)
      assert.strictEqual(edgeMap.get('chk_vr:2'), 'grd_has_muxincompat',
        'chk_vr NO must route to grd_has_muxincompat, not directly to ffs_001');

      // grd_has_muxincompat config
      const muxIncompat = pluginMap.get('grd_has_muxincompat');
      assert.ok(muxIncompat, 'Missing node grd_has_muxincompat');
      assert.strictEqual(muxIncompat.pluginName, 'checkStreamPropertyMultiValue',
        'grd_has_muxincompat must use checkStreamPropertyMultiValue');
      assert.strictEqual(muxIncompat.sourceRepo, 'Local',
        'grd_has_muxincompat must have sourceRepo "Local"');
      assert.strictEqual(muxIncompat.inputsDB.condition, 'includes',
        'grd_has_muxincompat must use "includes" condition');
      for (const codec of ['wma', 'adpcm', 'vorbis', 'opus']) {
        assert.ok(muxIncompat.inputsDB.valuesToMatch.includes(codec),
          `grd_has_muxincompat must match "${codec}"`);
      }

      // grd_has_muxincompat NO → ffs_001 (no mux-incompatible = normal path)
      assert.strictEqual(edgeMap.get('grd_has_muxincompat:2'), 'ffs_001',
        'grd_has_muxincompat NO should route to ffs_001 (normal path)');

      // grd_has_muxincompat YES → grd_has_safe_audio
      assert.strictEqual(edgeMap.get('grd_has_muxincompat:1'), 'grd_has_safe_audio',
        'grd_has_muxincompat YES should route to grd_has_safe_audio');

      // grd_has_safe_audio config
      const safeAudio = pluginMap.get('grd_has_safe_audio');
      assert.ok(safeAudio, 'Missing node grd_has_safe_audio');
      assert.strictEqual(safeAudio.pluginName, 'checkStreamPropertyMultiValue',
        'grd_has_safe_audio must use checkStreamPropertyMultiValue');
      assert.strictEqual(safeAudio.sourceRepo, 'Local',
        'grd_has_safe_audio must have sourceRepo "Local"');
      assert.strictEqual(safeAudio.inputsDB.condition, 'equals',
        'grd_has_safe_audio must use "equals" condition (avoids false positive on adpcm_ima_wav containing "pcm")');
      const safeValues = safeAudio.inputsDB.valuesToMatch.split(',').map(s => s.trim());
      for (const codec of ['aac', 'ac3', 'eac3', 'pcm_s16le', 'pcm_s24le']) {
        assert.ok(safeValues.includes(codec),
          `grd_has_safe_audio must match "${codec}"`);
      }

      // grd_has_safe_audio YES → ffs_001 (has safe audio = proceed to processing)
      assert.strictEqual(edgeMap.get('grd_has_safe_audio:1'), 'ffs_001',
        'grd_has_safe_audio YES should route to ffs_001 (proceed to processing)');

      // grd_has_safe_audio NO → channel-count split (converts instead of skipping)
      // EnsureAudioStream only matches undefined or configured language, so files
      // whose every audio stream is foreign-tagged cannot get an AAC/EAC3 track.
      // They keep today's manual-review path instead of ending as transcode errors.
      assert.strictEqual(edgeMap.get('grd_has_safe_audio:2'), 'grd_mux_lang_ok',
        'grd_has_safe_audio NO should route to the language guard first');

      const langOk = pluginMap.get('grd_mux_lang_ok');
      const langForeign = pluginMap.get('grd_mux_lang_foreign');
      assert.ok(langOk, 'Missing node grd_mux_lang_ok');
      assert.ok(langForeign, 'Missing node grd_mux_lang_foreign');
      assert.strictEqual(langOk.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(langForeign.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(langOk.sourceRepo, 'Local');
      assert.strictEqual(langForeign.sourceRepo, 'Local');
      assert.strictEqual(langOk.inputsDB.streamType, 'audio');
      assert.strictEqual(langOk.inputsDB.propertyToCheck, 'tags.language');
      assert.strictEqual(langOk.inputsDB.condition, 'includes');
      for (const lang of ['eng', 'und']) {
        assert.ok(langOk.inputsDB.valuesToMatch.split(',').includes(lang),
          `grd_mux_lang_ok must match "${lang}"`);
      }
      assert.strictEqual(langForeign.inputsDB.propertyToCheck, 'tags.language');
      assert.strictEqual(langForeign.inputsDB.condition, 'includes');
      for (const lang of ['pol', 'jpn', 'rus', 'hun', 'ger', 'fre', 'ita', 'cze']) {
        assert.ok(langForeign.inputsDB.valuesToMatch.split(',').includes(lang),
          `grd_mux_lang_foreign must match "${lang}"`);
      }
      // The denylist must never contain eng/und, or every file would be skipped.
      for (const lang of ['eng', 'und']) {
        assert.ok(!langForeign.inputsDB.valuesToMatch.split(',').includes(lang),
          `grd_mux_lang_foreign must NOT contain "${lang}"`);
      }

      assert.strictEqual(edgeMap.get('grd_mux_lang_ok:1'), 'grd_mux_ch6',
        'eng/und audio proceeds to the channel-count split');
      assert.strictEqual(edgeMap.get('grd_mux_lang_ok:2'), 'grd_mux_lang_foreign');
      // Foreign-tagged audio is re-tagged to "und" in a stream-copy pass rather
      // than diverted, so the encoder's language matcher can then select it.
      assert.strictEqual(edgeMap.get('grd_mux_lang_foreign:1'), 'cmt_retag',
        'foreign-tagged audio should enter the re-tag pass, not a dead end');

      const retagChain = [
        ['cmt_retag', 'ffs_retag'],
        ['ffs_retag', 'cmd_retag_container'],
        ['cmd_retag_container', 'cmd_retag_rmsub'],
        ['cmd_retag_rmsub', 'cmd_retag_rmdata'],
        ['cmd_retag_rmdata', 'cmd_retag_lang'],
        ['cmd_retag_lang', 'ffe_retag'],
        ['ffe_retag', 'grd_mux_ch6'],
      ];
      for (const [from, to] of retagChain) {
        assert.ok(pluginMap.get(from), `Missing node ${from}`);
        assert.strictEqual(edgeMap.get(`${from}:1`), to,
          `${from} should route to ${to}`);
      }

      const container = pluginMap.get('cmd_retag_container');
      assert.strictEqual(container.pluginName, 'ffmpegCommandSetContainer');
      assert.strictEqual(container.inputsDB.container, 'mkv',
        'mkv accepts every source codec combination in scope; pass 1 still sets mp4');
      assert.strictEqual(container.inputsDB.forceConform, 'false');

      const lang = pluginMap.get('cmd_retag_lang');
      assert.strictEqual(lang.pluginName, 'ffmpegCommandCustomArguments');
      assert.ok(lang.inputsDB.outputArguments.includes('-metadata:s:a language=und'),
        'cmd_retag_lang must rewrite the audio language to und');
      assert.ok(lang.inputsDB.outputArguments.includes('-c copy'),
        'the re-tag pass must be a stream copy');

      assert.strictEqual(pluginMap.get('ffs_retag').pluginName, 'ffmpegCommandStart');
      assert.strictEqual(pluginMap.get('ffe_retag').pluginName, 'ffmpegCommandExecute');
      assert.strictEqual(pluginMap.get('cmd_retag_rmdata').pluginName,
        'ffmpegCommandRemoveDataStreams');

      // Load-bearing: an encoder node here would transcode wmv3/vc1 sources
      // instead of copying them. Encoding belongs to pass 1.
      const retagNodes = ['cmt_retag', 'ffs_retag', 'cmd_retag_container',
        'cmd_retag_rmsub', 'cmd_retag_rmdata', 'cmd_retag_lang', 'ffe_retag'];
      for (const id of retagNodes) {
        assert.notStrictEqual(pluginMap.get(id).pluginName,
          'ffmpegCommandSetVideoEncoder',
          `${id} must not be a video encoder — the re-tag pass is a pure stream copy`);
        assert.notStrictEqual(pluginMap.get(id).pluginName,
          'ffmpegCommandEnsureAudioStream',
          `${id} must not create audio — that happens in pass 1`);
      }
      assert.strictEqual(edgeMap.get('grd_mux_lang_foreign:2'), 'grd_mux_ch6',
        'untagged audio proceeds — EnsureAudioStream matches undefined language');

      // Surround sources ride the existing EAC3 path; sub-6ch get a pass-1 AAC node.
      const ch6 = pluginMap.get('grd_mux_ch6');
      const ch8 = pluginMap.get('grd_mux_ch8');
      assert.ok(ch6, 'Missing node grd_mux_ch6');
      assert.ok(ch8, 'Missing node grd_mux_ch8');
      assert.strictEqual(ch6.pluginName, 'checkChannelCount');
      assert.strictEqual(ch8.pluginName, 'checkChannelCount');

      // Must mirror the EAC3 gates exactly, or the two conditions can drift apart.
      const eac3Ch6 = pluginMap.get('grd_eac3_ch');
      const eac3Ch8 = pluginMap.get('grd_eac3_ch8');
      assert.ok(eac3Ch6, 'Missing node grd_eac3_ch');
      assert.ok(eac3Ch8, 'Missing node grd_eac3_ch8');
      assert.strictEqual(ch6.inputsDB.channelCount, eac3Ch6.inputsDB.channelCount,
        'grd_mux_ch6 must mirror grd_eac3_ch channelCount');
      assert.strictEqual(ch8.inputsDB.channelCount, eac3Ch8.inputsDB.channelCount,
        'grd_mux_ch8 must mirror grd_eac3_ch8 channelCount');

      assert.strictEqual(edgeMap.get('grd_mux_ch6:1'), 'ffs_001',
        'grd_mux_ch6 YES (surround) goes straight to the pipeline');
      assert.strictEqual(edgeMap.get('grd_mux_ch6:2'), 'grd_mux_ch8');
      assert.strictEqual(edgeMap.get('grd_mux_ch8:1'), 'ffs_001',
        'grd_mux_ch8 YES (7.1) goes straight to the pipeline');
      assert.strictEqual(edgeMap.get('grd_mux_ch8:2'), 'var_need_p1_aac');
      assert.strictEqual(edgeMap.get('var_need_p1_aac:1'), 'ffs_001');

      // var_need_p1_aac sets the short name; chk_p1_aac reads the prefixed path.
      const setVar = pluginMap.get('var_need_p1_aac');
      assert.strictEqual(setVar.pluginName, 'setFlowVariable');
      assert.strictEqual(setVar.inputsDB.variable, 'need_p1_aac');
      assert.strictEqual(setVar.inputsDB.value, 'true');

      const chkVar = pluginMap.get('chk_p1_aac');
      assert.ok(chkVar, 'Missing node chk_p1_aac');
      assert.strictEqual(chkVar.pluginName, 'checkFlowVariable');
      assert.strictEqual(chkVar.inputsDB.variable, 'args.variables.user.need_p1_aac',
        'checkFlowVariable needs the full args.variables.user. prefix or it silently never matches');
      assert.strictEqual(chkVar.inputsDB.value, 'true');
      assert.strictEqual(chkVar.inputsDB.condition, '==');

      // Pass-1 AAC sits between cmt_reorder and cmd_reorder: after the EAC3
      // section, before reorder maps streams. Order matters for argument-build
      // sequencing and to keep the single -ac slot unambiguous — NOT because
      // cmd_rmmux would remove the source (EnsureAudioStream clones streams
      // regardless of their removed flag).
      assert.strictEqual(edgeMap.get('cmt_reorder:1'), 'chk_p1_aac',
        'cmt_reorder must now feed chk_p1_aac');
      assert.strictEqual(edgeMap.get('chk_p1_aac:1'), 'cmd_p1_aac');
      assert.strictEqual(edgeMap.get('chk_p1_aac:2'), 'cmd_reorder');
      assert.strictEqual(edgeMap.get('cmd_p1_aac:1'), 'cmd_reorder');

      const p1aac = pluginMap.get('cmd_p1_aac');
      assert.ok(p1aac, 'Missing node cmd_p1_aac');
      assert.strictEqual(p1aac.pluginName, 'ffmpegCommandEnsureAudioStream');
      assert.strictEqual(p1aac.inputsDB.audioEncoder, 'aac');
      assert.strictEqual(p1aac.inputsDB.channels, '2');
      assert.strictEqual(p1aac.inputsDB.language, '',
        'Empty language: these sources usually carry no language tag');
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

    test('VC-1/WMV sources bypass NVDEC hardware decoding', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // NVDEC on Turing silently emits a single frame for VC-1 in ASF, which the
      // encoder then duplicates for the whole duration (frozen video, ~88 B/frame).
      // Those sources must reach an encoder node that does NOT pass -hwaccel cuda.
      const guard = pluginMap.get('grd_vc1');
      assert.ok(guard, 'Missing node grd_vc1');
      assert.strictEqual(guard.pluginName, 'checkStreamPropertyMultiValue');
      assert.strictEqual(guard.inputsDB.streamType, 'video');
      assert.strictEqual(guard.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(guard.inputsDB.condition, 'includes');
      for (const codec of ['vc1', 'wmv']) {
        assert.ok(guard.inputsDB.valuesToMatch.split(',').includes(codec),
          `grd_vc1 should match ${codec} sources`);
      }

      const target = edgeMap.get('grd_vc1:1');
      assert.ok(target, 'grd_vc1 YES handle must be wired');
      const encoder = pluginMap.get(target);
      assert.ok(encoder, `grd_vc1 YES target ${target} missing`);
      assert.strictEqual(encoder.pluginName, 'ffmpegCommandSetVideoEncoder');
      assert.strictEqual(encoder.inputsDB.hardwareDecoding, 'false',
        'VC-1/WMV sources must be decoded in software');
      assert.strictEqual(encoder.inputsDB.forceEncoding, 'true',
        'VC-1/WMV sources cannot be stream-copied to HEVC');
    });

    test('every NVENC encoder reachable from the resolution tiers is accounted for', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));
      // Any encoder still using hardware decoding must only be reachable by
      // sources that NVDEC handles. Keep an explicit inventory so a new encoder
      // node cannot quietly inherit -hwaccel cuda.
      const hwDecoders = flow.flowPlugins
        .filter((p) => p.pluginName === 'ffmpegCommandSetVideoEncoder')
        .filter((p) => p.inputsDB.hardwareDecoding === 'true')
        .map((p) => p.id)
        .sort();
      assert.deepStrictEqual(hwDecoders,
        ['cmd_hevc_1080', 'cmd_hevc_4k', 'cmd_hevc_sd', 'cmd_vr_hevc'],
        'Unexpected encoder node with hardwareDecoding=true; confirm NVDEC can decode its sources');
      // The recovery paths must stay on software decoding.
      for (const id of ['cmd_hevc_force', 'cmd_retry_hevc']) {
        assert.strictEqual(pluginMap.get(id).inputsDB.hardwareDecoding, 'false',
          `${id} must decode in software so it can recover NVDEC failures`);
      }
    });

    test('undersized output fails the flow instead of replacing the original', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // A frozen-video encode lands around 3-4% of the source size, well under
      // any legitimate transcode, so the size ratio is the general safety net.
      const size = pluginMap.get('fl_size');
      assert.strictEqual(size.pluginName, 'compareFileSizeRatio');
      assert.ok(Number(size.inputsDB.greaterThan) >= 10,
        'fl_size needs a lower bound so frozen/blank output cannot pass');

      // Out of range → distinguish undersized from oversized.
      assert.strictEqual(edgeMap.get('fl_size:2'), 'fl_size_small');
      const small = pluginMap.get('fl_size_small');
      assert.strictEqual(small.pluginName, 'compareFileSizeRatio');
      assert.strictEqual(small.inputsDB.greaterThan, '0');
      assert.strictEqual(small.inputsDB.lessThan, String(size.inputsDB.greaterThan));

      // Oversized keeps the existing review path.
      assert.strictEqual(edgeMap.get('fl_size_small:2'), 'cmt_toobig');

      // Undersized must reach failFlow, not requireReview: Tdarr's global
      // "auto-approve successful transcodes" setting can bypass requireReview.
      let node = edgeMap.get('fl_size_small:1');
      const seen = new Set();
      while (node && !seen.has(node)) {
        seen.add(node);
        const plugin = pluginMap.get(node);
        assert.ok(plugin, `Missing node ${node}`);
        assert.notStrictEqual(plugin.pluginName, 'requireReview',
          'undersized output must not route to an auto-approvable review');
        if (plugin.pluginName === 'failFlow') break;
        node = edgeMap.get(`${node}:1`);
      }
      assert.ok(node && pluginMap.get(node).pluginName === 'failFlow',
        'undersized output must terminate in failFlow');
    });

    test('onFlowError gives up safely without replacing original file', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Walk every path reachable from err_on and verify that:
      //   (a) any reachable replaceOriginalFile is gated behind a successful
      //       retry health check (chk_health_retry handle 1) — never reached
      //       directly from the error handler with a possibly-broken working
      //       file, and
      //   (b) every terminal (node with no outgoing edges) reachable without
      //       passing chk_health_retry is a comment node, so giving up
      //       preserves the original file.
      const errEdges = flow.flowEdges.filter((e) => e.source === 'err_on');
      assert.ok(errEdges.length > 0, 'onFlowError must have at least one outgoing edge');

      const adjacency = new Map();
      for (const e of flow.flowEdges) {
        if (!adjacency.has(e.source)) adjacency.set(e.source, []);
        adjacency.get(e.source).push(e);
      }

      // BFS from each err_on target. State: node + whether we've passed the
      // retry-success gate (chk_health_retry handle 1).
      const visited = new Set();
      const queue = [];
      for (const edge of errEdges) {
        queue.push({ nodeId: edge.target, passedRetryGate: false });
      }

      while (queue.length > 0) {
        const { nodeId, passedRetryGate } = queue.shift();
        const key = `${nodeId}:${passedRetryGate}`;
        if (visited.has(key)) continue;
        visited.add(key);

        const node = pluginMap.get(nodeId);
        assert.ok(node, `Missing reachable node ${nodeId} from err_on`);

        // Safety: replaceOriginalFile must only be reachable after the retry
        // health check has confirmed the working file is valid.
        if (node.pluginName === 'replaceOriginalFile') {
          assert.ok(passedRetryGate,
            `replaceOriginalFile (${nodeId}) is reachable from onFlowError without passing chk_health_retry — a broken working file could overwrite the original`);
        }

        const outgoing = adjacency.get(nodeId) || [];
        if (outgoing.length === 0) {
          // Terminal reached. If we haven't passed the retry gate, this must
          // be a comment node (safe give-up), so the original file stays.
          if (!passedRetryGate) {
            assert.strictEqual(node.pluginName, 'comment',
              `onFlowError give-up terminal ${nodeId} must be a comment node to preserve the original file`);
          }
          continue;
        }

        for (const edge of outgoing) {
          let nowPassed = passedRetryGate;
          if (nodeId === 'chk_health_retry' && edge.sourceHandle === '1') {
            nowPassed = true;
          }
          queue.push({ nodeId: edge.target, passedRetryGate: nowPassed });
        }
      }
    });
  });
}
