/**
 * Unit tests for the checkFileDuration local flow plugin.
 *
 * A source with no usable duration pads the transcode timeline with duplicate
 * frames and holds a worker slot indefinitely (issue #96). This plugin is the
 * pre-flight guard, so the precedence between the three places a duration can
 * live, and the boundary around the threshold, both need locking in.
 */

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const pluginPath = join(__dirname, '..', 'plugins', 'LocalFlowPlugins',
  'file', 'checkFileDuration', '1.0.0', 'index.js');
const mod = await import(pathToFileURL(pluginPath).href);
const { plugin, details } = mod.default;

const run = (inputFileObj, minimumSeconds = '1') => plugin({
  inputFileObj,
  inputs: { minimumSeconds },
  variables: {},
  jobLog: () => {},
});

describe('checkFileDuration plugin', () => {
  test('details() returns valid metadata with two outputs', () => {
    const d = details();
    assert.ok(d.name);
    assert.ok(d.Description);
    assert.strictEqual(d.Outputs.length, 2);
  });

  describe('accepts a usable duration', () => {
    test('from ffProbeData.format.duration', () => {
      const r = run({ ffProbeData: { format: { duration: '1288.65' }, streams: [] } });
      assert.strictEqual(r.outputNumber, '1');
    });

    test('from the file record when the format block has none', () => {
      const r = run({ duration: 1288, ffProbeData: { format: {}, streams: [] } });
      assert.strictEqual(r.outputNumber, '1');
    });

    test('from a stream when neither of the others has one', () => {
      const r = run({ ffProbeData: { format: {}, streams: [{ duration: '600.0' }] } });
      assert.strictEqual(r.outputNumber, '1');
    });

    test('takes the longest stream duration', () => {
      const r = run({
        ffProbeData: { format: {}, streams: [{ duration: '0' }, { duration: '900.5' }] },
      });
      assert.strictEqual(r.outputNumber, '1');
    });
  });

  describe('rejects an unusable duration', () => {
    test('when nothing reports one', () => {
      const r = run({ duration: 0, ffProbeData: { format: {}, streams: [{ codec_type: 'video' }] } });
      assert.strictEqual(r.outputNumber, '2');
    });

    test('when ffprobe reports the literal string N/A', () => {
      const r = run({ ffProbeData: { format: { duration: 'N/A' }, streams: [] } });
      assert.strictEqual(r.outputNumber, '2');
    });

    test('when the duration is zero', () => {
      const r = run({ duration: 0, ffProbeData: { format: { duration: '0' }, streams: [] } });
      assert.strictEqual(r.outputNumber, '2');
    });

    test('when ffProbeData is missing entirely', () => {
      const r = run({});
      assert.strictEqual(r.outputNumber, '2');
    });

    test('when the duration is negative', () => {
      const r = run({ ffProbeData: { format: { duration: '-5' }, streams: [] } });
      assert.strictEqual(r.outputNumber, '2');
    });
  });

  describe('minimumSeconds threshold', () => {
    test('a duration above the threshold passes', () => {
      const r = run({ ffProbeData: { format: { duration: '31' }, streams: [] } }, '30');
      assert.strictEqual(r.outputNumber, '1');
    });

    test('a duration equal to the threshold is rejected', () => {
      const r = run({ ffProbeData: { format: { duration: '30' }, streams: [] } }, '30');
      assert.strictEqual(r.outputNumber, '2');
    });

    test('a non-numeric threshold falls back to the default rather than passing everything', () => {
      const r = run({ ffProbeData: { format: { duration: '0.5' }, streams: [] } }, 'abc');
      assert.strictEqual(r.outputNumber, '2');
    });

    test('a missing inputs object does not throw', () => {
      const r = plugin({
        inputFileObj: { ffProbeData: { format: { duration: '600' }, streams: [] } },
        variables: {},
        jobLog: () => {},
      });
      assert.strictEqual(r.outputNumber, '1');
    });
  });

  test('passes variables through untouched', () => {
    const variables = { user: { retry_encode: 'true' } };
    const r = plugin({
      inputFileObj: { ffProbeData: { format: { duration: '600' }, streams: [] } },
      inputs: { minimumSeconds: '1' },
      variables,
      jobLog: () => {},
    });
    assert.strictEqual(r.variables, variables);
  });

  test('real-world case: raw elementary stream carrying a .avi extension', () => {
    // ffprobe reports format_name=m4v, duration=N/A, no index, no audio.
    // These are the files that stalled workers for over 150 hours.
    const r = run({
      duration: 0,
      ffProbeData: {
        format: {},
        streams: [{ codec_type: 'video', codec_name: 'mpeg4', width: 512, height: 384 }],
      },
    });
    assert.strictEqual(r.outputNumber, '2',
      'a source with no duration anywhere must be rejected before transcoding');
  });

  test('real-world case: healthy sibling with a proper container still passes', () => {
    // The same episode in a real container must not be caught by the guard.
    const r = run({
      duration: 1795,
      ffProbeData: {
        format: { format_name: 'mov,mp4,m4a,3gp,3g2,mj2', duration: '1795.776000' },
        streams: [{ codec_type: 'video', codec_name: 'hevc', duration: '1795.733333' }],
      },
    });
    assert.strictEqual(r.outputNumber, '1');
  });
});
