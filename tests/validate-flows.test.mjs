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

      // All EAC3 paths merge at cmd_rmdata2 (before reorder)
      assert.strictEqual(edgeMap.get('cmd_eac3_eng:1'), 'cmd_rmdata2',
        'EAC3 eng path should route to cmd_rmdata2');
      assert.strictEqual(edgeMap.get('grd_eac3_codec:2'), 'cmd_rmdata2',
        'Non-surround codec path should route to cmd_rmdata2');
      assert.strictEqual(edgeMap.get('cmd_rm_eac3:1'), 'cmd_rmdata2',
        'cmd_rm_eac3 should route to cmd_rmdata2');

      // cmd_rm_eac3 still on non-surround path
      assert.strictEqual(edgeMap.get('grd_eac3_ch8:2'), 'cmd_rm_eac3',
        'Non-surround channel path should strip stereo EAC3 first');

      // Reorder THEN remove ac3/mp3 (prevents reorder from re-adding)
      assert.strictEqual(edgeMap.get('cmd_reorder2:1'), 'cmd_rm_ac3mp3',
        'cmd_reorder2 should route to cmd_rm_ac3mp3');
      assert.strictEqual(edgeMap.get('cmd_rm_ac3mp3:1'), 'cmd_faststart2',
        'cmd_rm_ac3mp3 should route to cmd_faststart2');
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

    test('attachments are stripped in first pipeline', () => {
      const edgeMap = new Map(flow.flowEdges.map((e) => [`${e.source}:${e.sourceHandle}`, e.target]));
      const pluginMap = new Map(flow.flowPlugins.map((p) => [p.id, p]));

      const node = pluginMap.get('cmd_rmattach');
      assert.ok(node, 'Missing node cmd_rmattach');
      assert.strictEqual(node.inputsDB.propertyToCheck, 'codec_type');
      assert.strictEqual(node.inputsDB.valuesToRemove, 'attachment');

      // cmd_rmimages → cmd_rmattach → cmt_nvenc
      assert.strictEqual(edgeMap.get('cmd_rmimages:1'), 'cmd_rmattach');
      assert.strictEqual(edgeMap.get('cmd_rmattach:1'), 'cmt_nvenc');
    });
  });
}
