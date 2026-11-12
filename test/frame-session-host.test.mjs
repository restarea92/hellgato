import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { registerHooks } from 'node:module';

const hooks = registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'hellgato') return { shortCircuit: true,
    url: 'data:text/javascript,export function createDeviceBridge(){throw Error("Bridge must not start before host admission")}' };
  return next(specifier, context);
} });
after(() => hooks.deregister());
const { startFullFrameSession } = await import('../apps/stream-dog/node/frame-session.mjs');
const worker = `
  console.log(JSON.stringify({event:'ready',protocolVersion:1,adapterId:'fixture',modelId:'fixture',layout:{width:1,height:1,rotation:0}}));
  let buffered='';process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>{
    buffered+=chunk;let end;while((end=buffered.indexOf('\\n'))>=0){
      const request=JSON.parse(buffered.slice(0,end));buffered=buffered.slice(end+1);
      if(request.method!=='close')throw Error('Physical device must not open before host admission');
      console.log(JSON.stringify({id:request.id,result:{status:'closed'}}));
    }
  });process.stdin.on('end',()=>process.exit(0));
`;

test('hardware mode reads the host executable and rejects changed or unprepared hosts before USB open', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'hellgato-host-guard-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const executable = join(directory, 'StreamDeck.exe'), bytes = Buffer.from('fixture host');
  await writeFile(executable, bytes);
  const config = { adapter: { command: process.execPath, args: ['-e', worker] }, target: {},
    host: { pid: 1, executable, executableSha256: '0'.repeat(64) },
    hostProbe: { command: process.execPath, args: ['-e', 'console.log(JSON.stringify({ready:false}))', '--'] } };
  await assert.rejects(startFullFrameSession(config), /Stream Deck executable changed/);
  config.host.executableSha256 = createHash('sha256').update(bytes).digest('hex');
  await assert.rejects(startFullFrameSession(config), /Current Stream Deck process is not prepared/);
});
