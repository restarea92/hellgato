import { appendFileSync, lstatSync, renameSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export function createTraceWriter(destination, maxBytes = 4 * 1024 * 1024) {
  const path = destination instanceof URL ? fileURLToPath(destination) : destination;
  return line => {
    try {
      if (Buffer.byteLength(line) > maxBytes) return;
      let size = 0;
      try {
        const info = lstatSync(path);
        if (!info.isFile() || info.isSymbolicLink()) return;
        size = info.size;
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (size + Buffer.byteLength(line) > maxBytes) {
        try {
          const info = lstatSync(`${path}.1`);
          if (!info.isFile() || info.isSymbolicLink()) return;
          unlinkSync(`${path}.1`);
        } catch (error) { if (error.code !== 'ENOENT') throw error; }
        if (size > maxBytes) unlinkSync(path);
        else renameSync(path, `${path}.1`);
      }
      appendFileSync(path, line, { mode: 0o600 });
    } catch { /* Diagnostic failures must not interrupt device traffic. */ }
  };
}
