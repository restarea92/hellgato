import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { adapterFixture, deferred, until } from './helpers/frame-runtime-fixture.mjs';
import { FrameRuntimeManager } from '../apps/stream-dog/node/frame-runtime-manager.mjs';

test('two physical identities get independent sessions and disconnecting one preserves the other', async t=>{
  const f=await adapterFixture(t);const live=new Map();
  const manager=new FrameRuntimeManager({stateDir:f.root,settingsPath:join(f.root,'settings.json'),builtInAdapters:[f.path],fake:true}, {
    discover:async()=>['one','two'].map(id=>({id,adapterId:f.manifest.id,modelId:'fixture',displayName:id})),
    createSession:async(config,{onState})=>{const done=deferred();const id=config.deviceId;live.set(id,config);onState({deviceConnected:true,hostConnected:true,framesSubmitted:1});return {completion:done.promise,stop:async()=>{live.delete(id);done.resolve();},configure:async()=>true};}
  });t.after(()=>manager.close());
  await manager.command('discover',{adapterId:f.manifest.id});
  await manager.command('connect-device',{adapterId:f.manifest.id,deviceId:'one'});
  await until(()=>live.size===1);
  await manager.command('connect-device',{adapterId:f.manifest.id,deviceId:'two'});
  await until(()=>live.size===2);
  assert.notEqual(live.get('one').target.config.childSerialNumber,live.get('two').target.config.childSerialNumber);
  assert.notEqual(live.get('one').childPort,live.get('two').childPort);
  assert.equal(manager.getState().sessions.length,1);
  await manager.command('disconnect-device',{adapterId:f.manifest.id,deviceId:'one'});
  assert.equal(live.has('one'),false);assert.equal(live.has('two'),true);
  await manager.close();assert.equal(live.size,0);
});
