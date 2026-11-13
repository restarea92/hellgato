import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { registerHooks } from 'node:module';

const hooks = registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'hellgato') return { shortCircuit: true,
    url: 'data:text/javascript,export function createDeviceBridge(){throw Error("Bridge fixture reached")}' };
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
