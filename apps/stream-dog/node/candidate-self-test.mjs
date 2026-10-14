import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createCandidateRunner } from './candidate-session.mjs';

/** Uses the real manifested/frozen worker with its explicit fake backend, while
 * replacing only the host bridge and read-only host inspection. No ports/USB. */
export function createCandidateSelfTest(config) {
  assert.equal(config.deviceId, 'fake:buttons-dial');
  const bridges = [];
  let inspections = 0;
  const runner = createCandidateRunner(config, { selfTest: true,
    readiness: async ({ profile }) => {
      inspections++;
      return { ready: true, targetId: profile.target.id, profileId: profile.id,
        preparation: ['streamdeck-plus-ten-key'], host: { pid: process.pid,
          creationTime: 'simulation-only', executableDigest: '0'.repeat(64) } };
    },
    bridgeFactory: options => {
      const bridge = new EventEmitter();
      bridge.options = options; bridge.inputs = []; bridge.sequence = 0;
      bridge.ports = { primary: null, child: null };
      bridge.start = async () => {};
      bridge.stop = async () => {};
      bridge.sendInput = event => { bridge.inputs.push(event); return true; };
      bridge.output = payload => bridge.emit('output', { ...payload, deviceId: options.device.id,
        sessionId: options.sessionId, generation: options.generation, sequence: ++bridge.sequence });
      bridges.push(bridge); return bridge;
    },
  });
  const waitUntil = async predicate => {
    const deadline = Date.now() + 10000;
    while (!await predicate()) {
      if (Date.now() > deadline || runner.getStatus().state === 'failed') throw new Error('Candidate self-test condition failed');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  };
  return { runner, async verify() {
    await runner.start();
    assert.equal(bridges.length, 1);
    const identity = bridges[0].options.target.config;
    assert.equal(identity.serialNumber, config.identity.dockSerial);
    assert.equal(identity.childSerialNumber, config.identity.childSerial);
    assert.deepEqual(identity.macAddress, config.identity.macAddress);
    const red = Buffer.from(Array(16).fill([255, 0, 0]).flat()).toString('base64');
    bridges[0].output({ type: 'frame', surfaceId: 'key:0', format: 'rgb888', width: 4, height: 4, data: red });
    bridges[0].output({ type: 'control', control: 'brightness', value: 37 });
    await waitUntil(() => runner.getStatus().counters.controlWrites === 1);
    assert.equal((await runner.getWorkerState()).frames['key:0'], red);
    await runner.control({ control: 'knobBrightness', value: 13 });
    await runner.control({ control: 'followDisplayBrightness', value: false });
    await runner.control({ control: 'sleepDuration', value: 60 });
    await runner.control({ control: 'sleep' });
    assert.equal((await runner.getWorkerState()).sleeping, true);
    await runner.control({ control: 'wake' });
    await runner.control({ control: 'screensaver' });
    assert.equal((await runner.getWorkerState()).sleeping, true);
    await runner.control({ control: 'wake' });
    bridges[0].emit('clientDisconnected', { role: 'child' });
    bridges[0].emit('clientConnected', { role: 'child' });
    await waitUntil(() => inspections === 2);
    await runner.reconnect();
    assert.equal(bridges.length, 2);
    assert.equal(inspections, 3);
    assert.equal(runner.getStatus().generation, 2);
    const state = await runner.getWorkerState();
    assert.equal(state.brightness, 37);
    assert.equal(state.knobBrightness, 13);
    assert.equal(state.followDisplayBrightness, false);
    assert.equal(state.sleepDuration, 60);
    assert.equal(state.frames['key:0'], red);
    assert.equal(bridges[1].inputs.length, 0);
    assert.equal(runner.getStatus().counters.unconfirmedWrites, 0);
    await runner.stop();
    return { ...runner.getStatus(), checks: ['verified-worker-manifest', 'fake-backend', 'explicit-identity',
      'fresh-host-reconnect-readiness', 'bounded-session-reconnect', 'settings-and-frame-restoration', 'no-input-replay', 'clean-stop'] };
  } };
}
