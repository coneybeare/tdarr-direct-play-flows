/* eslint-disable no-param-reassign */

function getNestedProp(obj, path) {
  return path.split('.').reduce((o, k) => (o != null ? o[k] : undefined), obj);
}

function details() {
  return {
    name: 'Check Stream Property (Multi-Value)',
    Operation: 'Filter',
    Description: 'Check if any stream has a property matching any of the comma-separated values.',
    Version: '1.0.0',
    Tags: 'audio,video,filter',
    Inputs: [
      {
        name: 'streamType',
        type: 'string',
        defaultValue: 'all',
        inputUI: { type: 'dropdown', options: ['all', 'video', 'audio', 'subtitle', 'data'] },
        tooltip: 'Which stream type to check',
      },
      {
        name: 'propertyToCheck',
        type: 'string',
        defaultValue: 'codec_name',
        inputUI: { type: 'text' },
        tooltip: 'Dot-notation property path (e.g. codec_name, tags.language)',
      },
      {
        name: 'valuesToMatch',
        type: 'string',
        defaultValue: '',
        inputUI: { type: 'text' },
        tooltip: 'Comma-separated values to check against',
      },
      {
        name: 'condition',
        type: 'string',
        defaultValue: 'includes',
        inputUI: { type: 'dropdown', options: ['includes', 'equals'] },
        tooltip: 'includes = substring match, equals = exact match',
      },
    ],
  };
}

function plugin(args) {
  const { streamType, propertyToCheck, valuesToMatch, condition } = args.inputs;
  const streams = (args.inputFileObj.ffProbeData || {}).streams || [];

  const filtered = streamType === 'all'
    ? streams
    : streams.filter((s) => s.codec_type === streamType);

  const values = (valuesToMatch || '')
    .split(',')
    .map((v) => v.trim().toLowerCase())
    .filter((v) => v.length > 0);

  if (values.length === 0) {
    return { outputFileObj: args.inputFileObj, outputNumber: '2', variables: args.variables };
  }

  for (const stream of filtered) {
    const prop = getNestedProp(stream, propertyToCheck);
    if (prop == null) continue;
    const propStr = String(prop).toLowerCase();

    for (const val of values) {
      if (condition === 'equals' && propStr === val) {
        return { outputFileObj: args.inputFileObj, outputNumber: '1', variables: args.variables };
      }
      if (condition === 'includes' && propStr.includes(val)) {
        return { outputFileObj: args.inputFileObj, outputNumber: '1', variables: args.variables };
      }
    }
  }

  return { outputFileObj: args.inputFileObj, outputNumber: '2', variables: args.variables };
}

export { details, plugin };
