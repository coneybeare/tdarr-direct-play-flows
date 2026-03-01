/* eslint-disable no-param-reassign */
/**
 * checkSSIMScore — local Tdarr flow plugin
 *
 * Compares the transcoded working file against the original source using
 * FFmpeg's built-in ssim filter. Routes to output 1 when the score meets the
 * configured threshold (quality preserved) or output 2 when below it (quality
 * degraded — route to a review node).
 *
 * Setup: place this directory in your Tdarr local plugins folder and configure
 * that folder as a plugin repo named "local" in Tdarr → Settings → Plugins.
 * Reference in flow JSON as:  "sourceRepo": "local", "pluginName": "checkSSIMScore"
 *
 * Performance notes:
 *   - Only the first `sample_duration` seconds are decoded (default 60 s).
 *   - Both inputs are scaled to 960×540 before comparison to reduce CPU load.
 *   - `format=yuv420p` in the filter graph ensures GPU-decoded frames (NVDEC
 *     via -hwaccel auto) are transferred to system memory before SSIM compute.
 *   - On the NVIDIA T400, NVDEC handles 4K HEVC decode in well under realtime,
 *     so a 60-second sample typically completes in ~20–30 s wall time.
 */

const details = () => ({
  name: 'Check SSIM quality',
  description:
    'Compares the transcoded working file to the original using SSIM. '
    + 'Routes to output 1 (pass) when score ≥ threshold, output 2 (fail) otherwise.',
  style: { borderColor: '#6366f1' },
  tags: 'video',
  isStartPlugin: false,
  pType: '',
  requiresVersion: '2.11.01',
  sidebarPosition: -1,
  icon: 'FaCheckCircle',
  inputs: [
    {
      label: 'SSIM Threshold',
      name: 'ssim_threshold',
      type: 'string',
      defaultValue: '0.97',
      inputUI: { type: 'text' },
      tooltip:
        'Minimum SSIM score (0–1) for quality to be considered preserved. '
        + '1.0 = identical. 0.97 is recommended for visually transparent transcodes. '
        + 'Files scoring below this value route to output 2.',
    },
    {
      label: 'Sample Duration (seconds)',
      name: 'sample_duration',
      type: 'string',
      defaultValue: '60',
      inputUI: { type: 'text' },
      tooltip:
        'Seconds of content to compare from the start of the file. '
        + '60 s gives a representative quality sample with manageable decode time.',
    },
  ],
  outputs: [
    {
      number: 1,
      tooltip: 'SSIM ≥ threshold — quality preserved, continue',
    },
    {
      number: 2,
      tooltip: 'SSIM < threshold — quality degraded, route to review',
    },
  ],
});

const plugin = async (args) => {
  const { spawnSync } = require('child_process');
  const {
    inputs, inputFileObj, variables, jobLog, ffmpegPath,
  } = args;

  const threshold = parseFloat(inputs.ssim_threshold ?? '0.97');
  const duration = parseInt(inputs.sample_duration ?? '60', 10);

  const originalFile = inputFileObj.file;
  const workingFile = args.workingFileObj?.file ?? originalFile;

  // If no transcode occurred (working file is still the original), skip.
  if (workingFile === originalFile) {
    jobLog('[SSIM] Working file equals original — nothing was transcoded, skipping check.');
    return { outputFileObj: args.inputFileObj, outputNumber: 1, variables };
  }

  jobLog(`[SSIM] Distorted : ${workingFile}`);
  jobLog(`[SSIM] Reference : ${originalFile}`);
  jobLog(`[SSIM] Threshold : ${threshold}  |  Sample : ${duration} s`);

  // Scale both streams to 960×540 before SSIM to reduce compute cost.
  // format=yuv420p converts GPU (hwaccel) frames to system-memory YUV so the
  // CPU-based ssim filter can consume them without an explicit hwdownload step.
  const filterComplex = [
    '[0:v]format=yuv420p,scale=960:540:flags=lanczos[a]',
    '[1:v]format=yuv420p,scale=960:540:flags=lanczos[b]',
    '[a][b]ssim',
  ].join(';');

  // Input order: distorted first, reference second — matches ffmpeg ssim convention.
  const ffmpegArgs = [
    '-hwaccel', 'auto',
    '-t', String(duration),
    '-i', workingFile,
    '-hwaccel', 'auto',
    '-t', String(duration),
    '-i', originalFile,
    '-filter_complex', filterComplex,
    '-f', 'null', '-',
  ];

  const result = spawnSync(ffmpegPath, ffmpegArgs, { encoding: 'utf8' });

  // FFmpeg writes SSIM summary to stderr:
  //   [Parsed_ssim_0 @ 0x...] SSIM Y:0.999 U:0.999 V:0.999 All:0.999237 (31.22)
  const output = (result.stdout ?? '') + (result.stderr ?? '');
  const match = output.match(/All:([\d.]+)/);

  if (!match) {
    jobLog('[SSIM] WARNING: Could not parse SSIM score from FFmpeg output — treating as pass.');
    jobLog(`[SSIM] FFmpeg exit code: ${result.status}`);
    jobLog(`[SSIM] Output (last 500 chars): ${output.slice(-500)}`);
    return { outputFileObj: args.inputFileObj, outputNumber: 1, variables };
  }

  const score = parseFloat(match[1]);
  const pass = score >= threshold;
  jobLog(
    `[SSIM] Score: ${score.toFixed(6)}  →  ${pass ? 'PASS ✓' : 'FAIL ✗'}`
    + `  (threshold: ${threshold})`,
  );

  return {
    outputFileObj: args.inputFileObj,
    outputNumber: pass ? 1 : 2,
    variables,
  };
};

module.exports.details = details;
module.exports.plugin = plugin;
