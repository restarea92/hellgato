import { createSession, createSidecarAdapter, type Adapter, type HostReadiness, type SidecarOptions } from '../packages/hellgato/index.js';
import { createButtonSimulator, createNonN4Simulator, createSimulatorProfile, replayInputs, runAdapterConformance } from '../packages/hellgato/src/testing/index.js';
import { createN4ProMappingProfile, createMiraboxN4ProAdapter } from '../packages/hellgato/src/adapters/mirabox-n4-pro/index.js';
const adapter: Adapter = createButtonSimulator();
const compact = createNonN4Simulator();
const profile = createSimulatorProfile(compact.device);
const report: HostReadiness = { ready: true, targetId: profile.target.id, profileId: profile.id, preparation: [] };
const session = createSession({ adapter: compact, deviceId: compact.device.id, profile, port: 0, childPort: 0, readiness: async () => report });
session.on('input', event => { const delivered: boolean = event.delivered; void delivered; });
session.on('state', state => { const generation: number = state.generation; void generation; });
session.on('diagnostic', diagnostic => { const code: string = diagnostic.code; void code; });
compact.emitInput({ type: 'key', index: 0, state: 'down' });
compact.emitInput({ type: 'dial', kind: 'rotate', index: 1, delta: 1 });
compact.emitInput({ type: 'touch', gesture: 'swipe', x: 1, y: 2, endX: 3, endY: 4 });
void replayInputs([], () => {}, { generation: 1 });
void runAdapterConformance(adapter, { deviceId: 'selected-device' });
void createN4ProMappingProfile({ virtualSerial: 'DOCK', childSerial: 'CHILD' });
const options: SidecarOptions = { command: '/explicit/worker', descriptor: adapter.descriptor };
void createSidecarAdapter(options);
// @ts-expect-error device selection must be explicit
createSession({ adapter, profile });
// @ts-expect-error mapping profile must be explicit
createSession({ adapter, deviceId: 'device' });
// @ts-expect-error fabricated input states are rejected by the public type surface
compact.emitInput({ type: 'key', index: 0, state: 'pressed' });
// @ts-expect-error N4 must bind a verified manifest
createMiraboxN4ProAdapter(options);
// @ts-expect-error virtual identity must be explicitly supplied
createN4ProMappingProfile({});
