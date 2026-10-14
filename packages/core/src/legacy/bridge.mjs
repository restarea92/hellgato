import { profile } from './profiles.mjs';
import { createDeviceBridge } from '../bridge/device-bridge.mjs';

/** @deprecated Use a facade mapping profile. Kept on the original root export. */
export function createN4ProProfile(identity = 2, dockIdentity = identity) {
  const device = profile('target', identity);
  device.config.serialNumber = profile('target', dockIdentity).config.serialNumber;
  return device;
}

/** @deprecated Use createDeviceBridge with an explicit device and host target.
 * This quarantined compatibility entry is the only N4-default core API. */
export async function createBridge(options = {}) {
  const defaults = createN4ProProfile();
  const geometry = { ...defaults.geometry, ...options.geometry };
  const config = { ...defaults.config, ...options.config };
  return createDeviceBridge({
    device: { id: 'legacy-n4-pro', adapterId: 'legacy', modelId: 'mirabox-n4-pro', capabilities: {
      keys: geometry.keyCount, dials: geometry.encoderCount,
      touch: geometry.touchWidth && geometry.touchHeight ? { width: geometry.touchWidth, height: geometry.touchHeight } : null,
      displays: [{ id: 'lcd', width: 800, height: 480, format: 'jpeg', partial: false }],
      dialPress: geometry.encoderCount ? 'pulse' : 'unsupported', dialRelease: 'unavailable',
    } },
    target: { id: 'legacy-streamdeck-plus', geometry, config, displays: [{ id: 'lcd', width: 800, height: 480, format: 'jpeg', partial: false }] },
    port: options.port, childPort: options.childPort, sleepDuration: options.sleepDuration,
  });
}
