/* eslint-disable no-param-reassign */

function details() {
  return {
    name: 'Reset to Original File',
    Operation: 'Transcode',
    Description: 'Reset the working file back to the original library file.'
      + ' Use this to retry processing from the original source after a failed pass.',
    Version: '1.0.0',
    Tags: 'tools',
    Inputs: [],
    Outputs: [
      { number: 1, tooltip: 'Continue with original file' },
    ],
  };
}

function plugin(args) {
  var orig = args.originalLibraryFile;
  var current = args.inputFileObj;

  args.jobLog('Resetting working file to original library file');
  args.jobLog('Original: ' + orig._id);
  args.jobLog('Current:  ' + current._id);

  return {
    outputFileObj: orig,
    outputNumber: '1',
    variables: args.variables,
  };
}

module.exports.details = details;
module.exports.plugin = plugin;
