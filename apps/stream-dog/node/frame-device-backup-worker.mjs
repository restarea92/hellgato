import { parentPort, workerData } from 'node:worker_threads';
import { createDeviceBackup, listDeviceBackups, manageDeviceBackup } from './frame-device-backups.mjs';

try {
  const result = workerData.action === 'create' ? await createDeviceBackup(workerData)
    : workerData.action === 'list' ? await listDeviceBackups(workerData.root, workerData.serial)
      : await manageDeviceBackup(workerData);
  parentPort.postMessage({ result });
} catch (error) { parentPort.postMessage({ error: error.message }); }
