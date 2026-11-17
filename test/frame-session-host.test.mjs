import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { registerHooks } from 'node:module';
import { FrameSessionSupervisor } from '../apps/stream-dog/node/frame-session-supervisor.mjs';
import { until } from './helpers/frame-runtime-fixture.mjs';

const hooks = registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'hellgato') return { shortCircuit: true,
    url: 'data:text/javascript,' + encodeURIComponent(`
      import { EventEmitter } from 'node:events';
      import { createServer } from 'node:net';
      export const bridges = [];
      export function createDeviceBridge(options) {
        if (!options.target.fixture) throw Error('Bridge fixture reached');
        const bridge = new EventEmitter(), servers = [createServer(), createServer()];
        Object.assign(bridge, { options, servers, ports: {}, releaseInputs() {}, submitInput() { return true; },
          async start() {
            await Promise.all(servers.map(server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve))));
            bridge.ports.primary = servers[0].address().port;
            bridge.primaryConnected = bridge.childConnected = true;
            bridge.emit('connection');
          },
          async stop() { await Promise.all(servers.map(server => new Promise(resolve => server.close(resolve)))); }
        });
        bridges.push(bridge); return bridge;
      }
    `) };
  return next(specifier, context);
} });
after(() => hooks.deregister());
const { startFullFrameSession } = await import('../apps/stream-dog/node/frame-session.mjs');
const worker = `
  console.log(JSON.stringify({event:'ready',protocolVersion:1,adapterId:'fixture',modelId:'fixture',layout:{width:1,height:1,rotation:0}}));
  let buffered='';process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>{
    buffered+=chunk;let end;while((end=buffered.indexOf('\\n'))>=0){
      const request=JSON.parse(buffered.slice(0,end));buffered=buffered.slice(end+1);
      const result=request.method==='discover'?{devices:[{id:'fixture',capabilities:{}}]}:request.method==='close'?{status:'closed'}:{};
      console.log(JSON.stringify({id:request.id,result}));
    }
  });process.stdin.on('end',()=>process.exit(0));
`;

test('untested host builds and missing layout preparation warn without blocking connection', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'hellgato-host-guard-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const executable = join(directory, 'StreamDeck.exe');
  await writeFile(executable, 'fixture host');
  const states = [];
  const config = { adapter: { command: process.execPath, args: ['-e', worker] }, target: {},
    host: { pid: 1, executable, executableSha256: '0'.repeat(64) },
    hostProbe: { command: process.execPath, args: ['-e', 'console.log(JSON.stringify({running:true,ready:false,tested:false,pid:1,created:"fixture"}))', '--'] } };
  await assert.rejects(startFullFrameSession(config, { onState: state => states.push(state) }), /Bridge fixture reached/);
  assert.deepEqual(states.at(-1).warnings.map(warning => warning.code), ['HOST_BUILD_UNTESTED', 'HOST_LAYOUT_UNPREPARED']);
  config.hostProbe.args[1] = 'console.log(JSON.stringify({running:false,ready:false}))';
  await assert.rejects(startFullFrameSession(config), /application is not running/);
});

test('USB loss without retention closes host listeners and automatically restores the same paired identity', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'hellgato-usb-reconnect-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const present = join(directory, 'usb-present');
  await writeFile(present, 'attached');
  const { bridges } = await import('hellgato');
  const usbWorker = `
    const fs = require('node:fs'), path = process.argv[1];
    const layout = { width: 1, height: 1, rotation: 0 };
    console.log(JSON.stringify({event:'ready',protocolVersion:1,adapterId:'fixture',modelId:'fixture',layout}));
    require('node:readline').createInterface({input:process.stdin}).on('line', line => {
      const r = JSON.parse(line);
      const result = r.method === 'discover' ? {devices:fs.existsSync(path)?[{id:'paired-usb',capabilities:{}}]:[]}
        : r.method === 'open' ? {layout} : r.method === 'close' ? {status:'closed'} : {};
      console.log(JSON.stringify({id:r.id,result}));
      if (r.method === 'open') setInterval(() => { if (!fs.existsSync(path)) process.exit(1); }, 20);
      if (r.method === 'close') process.exit(0);
    }).on('close', () => process.exit(0));
  `;
  let state;
  const config = { adapter: { command: process.execPath, args: ['-e', usbWorker, present] },
    target: { fixture: true, geometry: { encoderCount: 0 }, config: { childSerialNumber: 'HGMOCK000003' } },
    deviceId: 'paired-usb', keepConnected: false,
    host: { pid: 1, executable: present, executableSha256: '0'.repeat(64) },
    hostProbe: { command: process.execPath, args: ['-e', 'console.log(JSON.stringify({running:true,ready:true,tested:true,pid:1,created:"fixture"}))', '--'] } };
  const supervisor = new FrameSessionSupervisor({ retryMs: () => 100,
    start: ({ signal, generation }) => startFullFrameSession({ ...config, generation }, { signal, onState: value => { state = value; } }) });
  t.after(() => supervisor.disconnect());
  supervisor.connect();
  await until(() => supervisor.status().state === 'running' && state.hostConnected);
  const first = bridges.at(-1);
  assert.ok(first.servers.every(server => server.listening));
  await rm(present);
  await until(() => supervisor.status().state === 'waiting');
  assert.equal(state.deviceConnected, false);
  assert.equal(state.hostConnected, false);
  assert.equal(state.networkListening, false);
  assert.ok(first.servers.every(server => !server.listening));
  assert.equal(supervisor.status().desired, true);
  await writeFile(present, 'attached');
  await until(() => bridges.at(-1) !== first && supervisor.status().state === 'running');
  assert.equal(state.deviceConnected, true);
  assert.equal(state.hostConnected, true);
  const second = bridges.at(-1);
  assert.equal(second.options.device.id, first.options.device.id);
  assert.equal(second.options.target.config.childSerialNumber, first.options.target.config.childSerialNumber);
  assert.notEqual(second.options.sessionId, first.options.sessionId);
});
