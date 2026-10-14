import { readFileSync, writeFileSync, renameSync } from 'node:fs';

function validate(state) {
  if (!state || !Number.isInteger(state.sleepDuration) || state.sleepDuration < 0 || state.sleepDuration > 0x7fffffff) {
    throw new RangeError('Invalid saved sleep duration');
  }
  return { sleepDuration: state.sleepDuration };
}

export function loadDisplayState(path) {
  try { return validate(JSON.parse(readFileSync(path, 'utf8'))); }
  catch (error) {
    if (error.code === 'ENOENT') return { sleepDuration: 0 };
    throw error;
  }
}

export function saveDisplayState(path, state) {
  const temporary = new URL(path.href + '.tmp');
  writeFileSync(temporary, JSON.stringify(validate(state)) + '\n');
  renameSync(temporary, path);
}
