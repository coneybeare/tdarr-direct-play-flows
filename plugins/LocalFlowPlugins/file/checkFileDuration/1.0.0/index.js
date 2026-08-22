/* eslint-disable no-param-reassign */

// Smallest duration (seconds) we treat as usable. Anything at or below this is
// almost certainly "no duration" rather than a genuinely tiny clip.
const MIN_USABLE_SECONDS = 1;

function toSeconds(value) {
  const n = parseFloat(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// Duration can live in several places depending on how Tdarr probed the file.
// Take the best available; a raw elementary stream has none of them.
function resolveDuration(fileObj) {
  const probe = fileObj.ffProbeData || {};

  const fromFormat = toSeconds((probe.format || {}).duration);
  if (fromFormat > 0) return { seconds: fromFormat, source: 'ffProbeData.format.duration' };

  const fromRecord = toSeconds(fileObj.duration);
  if (fromRecord > 0) return { seconds: fromRecord, source: 'file.duration' };

  const streams = probe.streams || [];
  let best = 0;
  for (const stream of streams) {
    const d = toSeconds(stream.duration);
    if (d > best) best = d;
  }
  if (best > 0) return { seconds: best, source: 'stream.duration' };

  return { seconds: 0, source: 'none' };
}

function details() {
  return {
    name: 'Check File Duration',
    Operation: 'Filter',
    Description: 'Check whether the source has a usable duration. Raw elementary streams'
      + ' carrying a container extension (e.g. m4v named .avi) report none, and transcoding'
      + ' them pads the output with duplicate frames until the worker is effectively stuck.',
    Version: '1.0.0',
    Tags: 'video,filter',
    Inputs: [
      {
        name: 'minimumSeconds',
        type: 'string',
        defaultValue: '1',
        inputUI: { type: 'text' },
        tooltip: 'Durations at or below this many seconds count as unusable',
      },
    ],
    Outputs: [
      { number: 1, tooltip: 'File has a usable duration' },
      { number: 2, tooltip: 'File has no usable duration' },
    ],
  };
}

function plugin(args) {
  const configured = parseFloat((args.inputs || {}).minimumSeconds);
  const minimum = Number.isFinite(configured) && configured > 0
    ? configured
    : MIN_USABLE_SECONDS;

  const { seconds, source } = resolveDuration(args.inputFileObj);

  if (seconds > minimum) {
    args.jobLog(`Duration ${seconds.toFixed(2)}s (from ${source})`);
    return { outputFileObj: args.inputFileObj, outputNumber: '1', variables: args.variables };
  }

  args.jobLog(
    `No usable duration (best ${seconds.toFixed(2)}s from ${source}, minimum ${minimum}s). `
    + 'Transcoding this would pad the output with duplicate frames and stall the worker.',
  );
  return { outputFileObj: args.inputFileObj, outputNumber: '2', variables: args.variables };
}

// Tdarr loads plugins via require() (CJS), not import (ESM)
module.exports = { details, plugin };
