import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url)),args=process.argv.slice(2),index=args.indexOf('--bundle');
assert.ok(index>=0 && args[index+1],'--bundle is required');const bundle=resolve(args[index+1]);
const {startFrameRuntime}=await import(pathToFileURL(resolve(bundle,'runtime/frame-runtime.mjs')));
const template=JSON.parse(readFileSync(resolve(bundle,'runtime.json'),'utf8'));
mkdirSync(resolve(root,'work'),{recursive:true});const stateDir=mkdtempSync(resolve(root,'work/frame-fleet-smoke-'));
const config={...template.service,stateDir,settingsPath:resolve(stateDir,'settings.json'),builtInAdapters:template.adapters.map(path=>resolve(bundle,path)),fake:true,previewEnabled:false,apiPort:0};
let runtime=await startFrameRuntime(config);
const wait=async predicate=>{const deadline=Date.now()+15000;while(Date.now()<deadline){const value=runtime.manager.getState();if(predicate(value))return value;await new Promise(yes=>setTimeout(yes,20));}throw new Error('Concurrent runtime did not settle');};
const command=(action,params={})=>runtime.manager.command(action,params);
try {
  for(const adapterId of ['mirabox','hellgato-simulator']){const result=await command('discover',{adapterId});const device=result.devices.find(device=>device.adapterId===adapterId);await command('connect-device',{adapterId,deviceId:device.id});}
  let state=await wait(value=>value.screensaverRestores>=1 && value.sessions[0]?.screensaverRestores>=1);
  assert.deepEqual([state.display.width,state.sessions[0].display.width],[800,600]);
  const identities=[state.settings.identity.childSerial,state.sessions[0].settings.identity.childSerial];assert.notEqual(...identities);
  const secondary=state.sessions[0].adapterPid,secondaryAttempt=state.sessions[0].runtime.attempt;process.kill(state.adapterPid);
  state=await wait(value=>value.runtime.attempt>1 && value.runtime.state==='running' && value.screensaverRestores>=1);
  assert.equal(state.sessions[0].adapterPid,secondary);assert.equal(state.sessions[0].runtime.attempt,secondaryAttempt);
  await runtime.close();runtime=await startFrameRuntime(config);
  state=await wait(value=>value.screensaverRestores>=1 && value.sessions[0]?.screensaverRestores>=1);
  assert.deepEqual([state.settings.identity.childSerial,state.sessions[0].settings.identity.childSerial],identities);
  await command('disconnect-device',{adapterId:'mirabox',deviceId:state.settings.selectedDeviceId});
  state=runtime.manager.getState();assert.equal(state.runtime.desired,false);assert.equal(state.sessions[0].runtime.state,'running');
  const result={simulatedDevices:2,independentCanvases:true,independentRecovery:true,identityPreserved:true,independentDisconnect:true,previewEnabled:false,stateDir};
  writeFileSync(resolve(stateDir,'result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{await runtime.close();}
