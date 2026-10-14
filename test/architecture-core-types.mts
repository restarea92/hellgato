import { createDeviceBridge, createBridge, createN4ProProfile, type Bridge } from '../packages/core/src/bridge.mjs';
import { validateDeviceDescriptor, type DeviceDescriptor, type InputEvent, type MappingProfile, type DisplayFrame } from '../packages/core/src/contracts/index.mjs';
const device: DeviceDescriptor = validateDeviceDescriptor({});
const profile: MappingProfile = {
  id: 'example', version: '1.0.0', target: { id: 'streamdeck-test', ...createN4ProProfile() },
  controls: { keys: [0], dials: [] }, requirements: {},
};
const bridge: Bridge = createDeviceBridge({ device, target: profile.target });
void bridge.start();
bridge.on('output', output => { const frame: DisplayFrame | undefined = output.type === 'frame' ? output : undefined; void frame; });
const input: InputEvent = { deviceId: device.id, generation: 1, sequence: 0, type: 'key', index: 0, state: 'down' };
bridge.submitInput(input);
void createBridge();
// @ts-expect-error The neutral API never supplies a default device.
createDeviceBridge();
// @ts-expect-error Target selection is required.
createDeviceBridge({ device });
// @ts-expect-error Pulse is not a physical release.
const invalidPulse: InputEvent = { deviceId: 'x', generation: 1, sequence: 1, type: 'dial', kind: 'pulse', index: 0, state: 'up' };
// @ts-expect-error Touch swipes require both endpoints.
const invalidSwipe: InputEvent = { deviceId: 'x', generation: 1, sequence: 1, type: 'touch', gesture: 'swipe', x: 0, y: 0 };
void invalidPulse; void invalidSwipe;

const knob: import('../packages/core/src/contracts/index.mjs').ControlCommand = { type: 'control', control: 'knobBrightness', value: 50 };
const follow: import('../packages/core/src/contracts/index.mjs').ControlCommand = { type: 'control', control: 'followDisplayBrightness', value: false };
// @ts-expect-error Follow-display control uses a boolean, never a numeric brightness.
const invalidFollow: import('../packages/core/src/contracts/index.mjs').ControlCommand = { type: 'control', control: 'followDisplayBrightness', value: 1 };
// @ts-expect-error Screensaver control never carries a brightness payload.
const invalidSaver: import('../packages/core/src/contracts/index.mjs').ControlCommand = { type: 'control', control: 'screensaver', value: 1 };
void knob; void follow; void invalidFollow; void invalidSaver;
