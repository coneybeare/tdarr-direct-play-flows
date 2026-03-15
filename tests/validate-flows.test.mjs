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

    test('cmd_rm_ac3mp3 runs after reorder to prevent re-mapping', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_eac3_codec YES → grd_eac3_ch (surround codec found, check channels)
      assert.strictEqual(edgeMap.get('grd_eac3_codec:1'), 'grd_eac3_ch',
        'Surround codec path should route to channel check');

      // grd_eac3_codec must match both ac3 and eac3
      const codecGuard = pluginMap.get('grd_eac3_codec');
      assert.ok(codecGuard, 'Missing node grd_eac3_codec');
      assert.ok(codecGuard.inputsDB.valuesToMatch.includes('ac3'),
        'grd_eac3_codec must match ac3');
      assert.ok(codecGuard.inputsDB.valuesToMatch.includes('eac3'),
        'grd_eac3_codec must match eac3');
      assert.strictEqual(codecGuard.inputsDB.condition, 'includes',
        'grd_eac3_codec must use "includes" (not "equals") for comma-separated valuesToMatch');

      // Surround channels route through cmd_rm_old_eac3 before creating fresh EAC3
      assert.strictEqual(edgeMap.get('grd_eac3_ch:1'), 'cmd_rm_old_eac3',
        '6+ ch surround should strip old EAC3 first');
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:1'), 'cmd_rm_old_eac3',
        '8 ch surround should strip old EAC3 first');
      assert.strictEqual(edgeMap.get('cmd_rm_old_eac3:1'), 'cmd_eac3_eng',
        'After stripping old EAC3, create fresh 5.1');

      // Verify cmd_rm_old_eac3 config
      const rmOldEac3 = pluginMap.get('cmd_rm_old_eac3');
      assert.ok(rmOldEac3, 'Missing node cmd_rm_old_eac3');
      assert.strictEqual(rmOldEac3.pluginName, 'ffmpegCommandRemoveStreamByProperty');
      assert.strictEqual(rmOldEac3.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(rmOldEac3.inputsDB.valuesToRemove, 'eac3');
      assert.strictEqual(rmOldEac3.inputsDB.condition, 'includes');

      // All EAC3 paths merge at cmt_audio (EAC3 now in pass 1, before AAC stereo)
      assert.strictEqual(edgeMap.get('cmd_eac3_eng:1'), 'cmt_audio',
        'EAC3 eng path should route to cmt_audio');
      assert.strictEqual(edgeMap.get('grd_eac3_codec:2'), 'cmt_audio',
        'Non-surround codec path should route to cmt_audio');
      assert.strictEqual(edgeMap.get('cmd_rm_eac3:1'), 'cmt_audio',
        'cmd_rm_eac3 should route to cmt_audio');

      // cmd_rm_eac3 on non-surround path (no 6+ch surround, strip all eac3)
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:2'), 'cmd_rm_eac3',
        'Non-surround channel path should strip EAC3 first');

      // Reorder THEN remove ac3/mp3 (prevents reorder from re-adding)
      assert.strictEqual(edgeMap.get('cmd_reorder2:1'), 'cmd_rm_ac3mp3',
        'cmd_reorder2 should route to cmd_rm_ac3mp3');
      assert.strictEqual(edgeMap.get('cmd_rm_ac3mp3:1'), 'cmd_faststart2',
        'cmd_rm_ac3mp3 should route to cmd_faststart2');
    });

    test('guard chain catches orphaned stereo EAC3', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_ch YES → grd_surr_ch (check for 6+ ch surround)
      assert.strictEqual(edgeMap.get('grd_ch:1'), 'grd_surr_ch',
        '2ch check should route to surround channel check');

      // grd_surr_ch YES → grd_unwanted (has surround, check for unwanted audio)
      assert.strictEqual(edgeMap.get('grd_surr_ch:1'), 'grd_unwanted',
        'Files with surround should check for unwanted audio');

      // grd_surr_ch NO → grd_has_eac3 (no surround, check for orphaned EAC3)
      assert.strictEqual(edgeMap.get('grd_surr_ch:2'), 'grd_has_eac3',
        'No surround should check for orphaned EAC3');

      // grd_has_eac3 YES → cmt_proc (stereo EAC3 without surround = needs cleanup)
      assert.strictEqual(edgeMap.get('grd_has_eac3:1'), 'cmt_proc',
        'Orphaned EAC3 should route to processing');

      // grd_has_eac3 NO → grd_unwanted (no EAC3, check for unwanted audio)
      assert.strictEqual(edgeMap.get('grd_has_eac3:2'), 'grd_unwanted',
        'No EAC3 should check for unwanted audio');

      // Verify grd_has_eac3 config matches eac3
      const hasEac3 = pluginMap.get('grd_has_eac3');
      assert.ok(hasEac3, 'Missing node grd_has_eac3');
      assert.strictEqual(hasEac3.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(hasEac3.inputsDB.valuesToMatch, 'eac3');
      assert.strictEqual(hasEac3.inputsDB.condition, 'includes');
    });

    test('guard chain catches unwanted audio codecs', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // grd_unwanted YES → cmt_proc (has unwanted audio, needs cleanup)
      assert.strictEqual(edgeMap.get('grd_unwanted:1'), 'cmt_proc',
        'Unwanted audio should route to processing');

      // grd_unwanted NO → cmt_optimal (clean audio, already optimal)
      assert.strictEqual(edgeMap.get('grd_unwanted:2'), 'cmt_optimal',
        'No unwanted audio should be optimal');

      // Verify grd_unwanted config
      const grdUnwanted = pluginMap.get('grd_unwanted');
      assert.ok(grdUnwanted, 'Missing node grd_unwanted');
      assert.strictEqual(grdUnwanted.inputsDB.streamType, 'audio');
      assert.strictEqual(grdUnwanted.inputsDB.propertyToCheck, 'codec_name');
      assert.strictEqual(grdUnwanted.inputsDB.condition, 'includes');
      // Should catch common unwanted codecs
      for (const codec of ['ac3', 'dts', 'dca', 'mp3', 'truehd', 'flac']) {
        assert.ok(grdUnwanted.inputsDB.valuesToMatch.includes(codec),
          `grd_unwanted should catch ${codec}`);
      }

      // Every codec in the guard must be removable by the pipeline (exact match)
      const rmaudio = pluginMap.get('cmd_rmaudio');
      const rmac3mp3 = pluginMap.get('cmd_rm_ac3mp3');
      assert.ok(rmaudio && rmac3mp3, 'Missing removal nodes');
      const removableSet = new Set([
        ...rmaudio.inputsDB.valuesToRemove.split(',').map(s => s.trim()),
        ...rmac3mp3.inputsDB.valuesToRemove.split(',').map(s => s.trim()),
      ]);
      for (const codec of grdUnwanted.inputsDB.valuesToMatch.split(',').map(s => s.trim())) {
        assert.ok(removableSet.has(codec),
          `grd_unwanted catches "${codec}" but no removal node strips it`);
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

      // grd_fb_eng YES → cmt_reorder (eng pass created AAC)
      assert.strictEqual(edgeMap.get('grd_fb_eng:1'), 'cmt_reorder',
        'Eng audio present should skip fallback');

      // grd_fb_eng NO → cmd_ens_fb (fallback AAC creation)
      assert.strictEqual(edgeMap.get('grd_fb_eng:2'), 'cmd_ens_fb',
        'No eng/und audio should route to fallback AAC');

      // cmd_ens_fb → cmt_reorder
      assert.strictEqual(edgeMap.get('cmd_ens_fb:1'), 'cmt_reorder',
        'Fallback AAC should route to reorder');

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

    test('DTS is stripped by both audio removal nodes', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const rmaudio = pluginMap.get('cmd_rmaudio');
      assert.ok(rmaudio, 'Missing node cmd_rmaudio');
      assert.ok(rmaudio.inputsDB.valuesToRemove.includes('dts'),
        'cmd_rmaudio must remove dts (ffprobe codec_name variant)');

      const rmac3mp3 = pluginMap.get('cmd_rm_ac3mp3');
      assert.ok(rmac3mp3, 'Missing node cmd_rm_ac3mp3');
      assert.ok(rmac3mp3.inputsDB.valuesToRemove.includes('dts'),
        'cmd_rm_ac3mp3 must remove dts (reorder can re-map from first pipeline)');
    });

    test('second pipeline has faststart', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // cmd_faststart2 → ffe_reorder
      assert.strictEqual(edgeMap.get('cmd_faststart2:1'), 'ffe_reorder');

      const node = pluginMap.get('cmd_faststart2');
      assert.ok(node, 'Missing node cmd_faststart2');
      assert.ok(node.inputsDB.outputArguments.includes('+faststart'),
        'cmd_faststart2 must include +faststart');
    });

    test('remux passes include hvc1 tag', () => {
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      // Normal path remux
      const faststart2 = pluginMap.get('cmd_faststart2');
      assert.ok(faststart2, 'Missing node cmd_faststart2');
      assert.ok(faststart2.inputsDB.outputArguments.includes('-tag:v hvc1'),
        'cmd_faststart2 must include -tag:v hvc1 to prevent FFmpeg defaulting to hev1');

      // VR path remux
      const vrFaststart2 = pluginMap.get('cmd_vr_faststart2');
      assert.ok(vrFaststart2, 'Missing node cmd_vr_faststart2');
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

    test('ffmpegCommandRemoveStreamByProperty nodes use includes condition', () => {
      // All RemoveStreamByProperty nodes must use "includes" — "equals" silently fails
      const removeNodes = flow.flowPlugins.filter(
        (p) => p.pluginName === 'ffmpegCommandRemoveStreamByProperty'
      );
      for (const node of removeNodes) {
        assert.strictEqual(node.inputsDB.condition, 'includes',
          `${node.id} must use "includes" — "equals" silently fails on ffmpegCommandRemoveStreamByProperty`);
      }
    });
  });
}
