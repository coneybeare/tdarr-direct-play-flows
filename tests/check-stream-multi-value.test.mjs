/**
 * Unit tests for the checkStreamPropertyMultiValue local flow plugin.
 *
 * Tests the plugin function directly with mock args objects,
 * verifying multi-value matching with both "equals" and "includes" conditions.
 */

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Import the plugin module
const pluginPath = join(__dirname, '..', 'plugins', 'LocalFlowPlugins',
  'file', 'checkStreamPropertyMultiValue', '1.0.0', 'index.js');
// Plugin uses module.exports (CJS) for Tdarr compatibility; dynamic import wraps CJS as .default
const mod = await import(pathToFileURL(pluginPath).href);
const { plugin, details } = mod.default;

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
