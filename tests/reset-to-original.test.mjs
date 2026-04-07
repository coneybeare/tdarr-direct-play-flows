/**
 * Unit tests for the resetToOriginalFile local flow plugin.
 */

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const pluginPath = join(__dirname, '..', 'plugins', 'LocalFlowPlugins',
  'tools', 'resetToOriginalFile', '1.0.0', 'index.js');
const mod = await import(pathToFileURL(pluginPath).href);
const { plugin, details } = mod.default;

describe('resetToOriginalFile plugin', () => {
  test('details() returns valid metadata', () => {
    const d = details();
    assert.ok(d.name);
    assert.ok(d.Description);
    assert.ok(d.Outputs.length >= 1);
  });

  test('plugin() returns original library file as output', () => {
    const originalFile = { _id: '/movies/Test.mkv', file: '/movies/Test.mkv' };
    const workingFile = { _id: '/cache/Test.mp4', file: '/cache/Test.mp4' };
    const args = {
      originalLibraryFile: originalFile,
      inputFileObj: workingFile,
      variables: { user: { retry_encode: 'true' } },
      jobLog: () => {},
    };

    const result = plugin(args);

    assert.strictEqual(result.outputFileObj, originalFile,
      'Should return the original library file');
    assert.strictEqual(result.outputNumber, '1',
      'Should output on handle 1');
    assert.deepStrictEqual(result.variables, args.variables,
      'Should preserve variables');
  });

  test('plugin() does not return the working file', () => {
    const originalFile = { _id: '/movies/Original.mkv' };
    const workingFile = { _id: '/cache/Corrupt.mp4' };
    const args = {
      originalLibraryFile: originalFile,
      inputFileObj: workingFile,
      variables: {},
      jobLog: () => {},
    };

    const result = plugin(args);

    assert.notStrictEqual(result.outputFileObj, workingFile,
      'Should NOT return the corrupt working file');
  });
});
