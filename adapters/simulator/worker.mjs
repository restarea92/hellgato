import { createInterface } from 'node:readline';

const layout = { width: 600, height: 360, rotation: 0,
  keys: Array.from({ length: 8 }, (_, index) => ({ id: `key:${index}`,
    rect: [9 + index % 4 * 144, 9 + Math.floor(index / 4) * 114, 96, 96], sourceWidth: 96, sourceHeight: 96 })),
  strip: { x: 0, y: 266, width: 600, height: 80, logicalWidth: 600, logicalHeight: 80 } };
const capabilities = { keys: 8, dials: 4, touch: { width: 600, height: 80 },
  displays: [...layout.keys.map(key => ({ id: key.id, width: 96, height: 96, format: 'jpeg', partial: false })),
    { id: 'touch', width: 600, height: 80, format: 'jpeg', partial: true },
    { id: 'lcd', width: 600, height: 360, format: 'jpeg', partial: false }], dialPress: 'pulse', dialRelease: 'unavailable' };
const device = { id: 'simulator-1', adapterId: 'hellgato-simulator', modelId: 'eight-key-demo',
  firmware: 'simulator-1', displayName: '8-key simulator', capabilities };
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
let opened = false, sessionId, generation, sequence = 0, frames = 0;
emit({ event: 'ready', protocolVersion: 1, adapterId: device.adapterId, modelId: device.modelId, layout, simulation: true });
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on('line', line => {
  if (Buffer.byteLength(line) > 1500000) { process.exitCode = 1; lines.close(); process.stdin.destroy(); return; }
  let value;
  try {
    value = JSON.parse(line);
    if (!Number.isSafeInteger(value.id) || value.id <= 0 || typeof value.method !== 'string') throw new Error('Invalid envelope');
    const params = value.params ?? {};
    let result;
    switch (value.method) {
      case 'discover': result = { devices: [device] }; break;
      case 'describe': result = { layout, capabilities }; break;
      case 'open':
        if (params.deviceId !== device.id) throw new Error('Unknown simulator device');
        opened = true; sessionId = params.sessionId; generation = params.generation; sequence = 0;
        result = { layout, firmware: device.firmware }; break;
      case 'frame': {
        if (!opened || params.region !== undefined || params.width !== 600 || params.height !== 360 ||
            params.generation !== undefined && params.generation !== generation) throw new Error('Simulator accepts only a full current frame');
        const jpeg = Buffer.from(params.jpeg, 'base64');
        if (jpeg.length > 1048576 || jpeg[0] !== 255 || jpeg[1] !== 216) throw new Error('Invalid JPEG frame');
        result = { status: 'applied', verification: 'simulation', frames: ++frames }; break;
      }
      case 'control':
        if (!opened || !['brightness', 'sleep', 'wake'].includes(params.control)) throw new Error('Unsupported control');
        result = { status: 'applied', verification: 'simulation' }; break;
      case 'input':
        if (!opened) throw new Error('Simulator is closed');
        emit({ ...params, event: 'input', deviceId: device.id, generation, sessionId, sequence: ++sequence });
        result = { emitted: true }; break;
      case 'close': opened = false; result = { status: 'closed' }; break;
      default: throw new Error('Unsupported simulator method');
    }
    emit({ id: value.id, result });
  } catch (error) { emit({ id: value?.id ?? null, error: { code: 'SIMULATOR_CONTRACT', message: error.message } }); }
});
