import { parentPort, workerData } from 'node:worker_threads';
import { inspectAdapterPackage, installAdapterPackage } from './frame-adapter-package.mjs';
try {
  const review=inspectAdapterPackage(workerData.path);
  let result=review;
  if(workerData.action==='install') {
    if(workerData.reserved.includes(review.id)) throw new Error('User packages cannot replace a bundled official adapter');
    result={manifestPath:installAdapterPackage(workerData.path,workerData.destination,workerData.packageHash),id:review.id};
  }else if(workerData.action!=='inspect') throw new Error('Unknown adapter package task');
  parentPort.postMessage({result});
}catch(error){parentPort.postMessage({error:error.message});}
