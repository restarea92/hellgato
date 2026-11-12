import { resolve } from 'node:path';
import { packAdapter } from '../../apps/stream-dog/node/frame-adapter-package.mjs';
const [directory, output] = process.argv.slice(2);
if (!directory || !output || !output.endsWith('.hga')) throw new Error('Use pack-adapter.mjs DIRECTORY OUTPUT.hga');
console.log(JSON.stringify(packAdapter(resolve(directory), resolve(output))));
