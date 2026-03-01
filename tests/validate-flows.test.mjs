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
  });
}
