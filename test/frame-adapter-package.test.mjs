import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { adapterFixture } from './helpers/frame-runtime-fixture.mjs';
import { inspectAdapterPackage, installAdapterPackage, packAdapter } from '../apps/stream-dog/node/frame-adapter-package.mjs';

test('hga review warns for DLLs, pins the reviewed bytes and installs an independent complete pack', async t => {
  const f=await adapterFixture(t), path=join(f.root,'adapter.hga');
  packAdapter(f.root,path); const review=inspectAdapterPackage(path);
  assert.equal(review.source,'user'); assert.equal(review.safety,'unverified'); assert.match(review.warning,/DLL/);
  assert.deepEqual(review.dlls,['vendor/transport.dll']);
  assert.throws(()=>installAdapterPackage(path,join(f.root,'installed'),'0'.repeat(64)),/Review this exact/);
  const installed=installAdapterPackage(path,join(f.root,'installed'),review.packageHash);
  assert.equal(JSON.parse(readFileSync(installed)).id,f.manifest.id);
  assert.equal(installAdapterPackage(path,join(f.root,'installed'),review.packageHash),installed);
  const damaged=Buffer.from(readFileSync(path));damaged[60]^=1;writeFileSync(join(f.root,'broken.hga'),damaged);
  assert.throws(()=>inspectAdapterPackage(join(f.root,'broken.hga')));
});

test('hga rejects traversal and native special entries before extraction', async t=>{
  const f=await adapterFixture(t),path=join(f.root,'adapter.hga');packAdapter(f.root,path);
  const original=readFileSync(path);const changed=Buffer.from(original);
  for(let at=0;at<changed.length-46;at++)if(changed.readUInt32LE(at)===0x02014b50){const length=changed.readUInt16LE(at+28);if(length>=3){changed.write('../',at+46);break;}}
  writeFileSync(join(f.root,'unsafe.hga'),changed);assert.throws(()=>inspectAdapterPackage(join(f.root,'unsafe.hga')),/unsafe/);
});
