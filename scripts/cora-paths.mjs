import { relative } from 'node:path';

// Provenance and package paths use POSIX separators on every build platform.
export function coraModulePath(sourceRoot, file, relativePath = relative) {
  return relativePath(sourceRoot, file).replaceAll('\\', '/');
}
