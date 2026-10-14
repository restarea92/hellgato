import type { AdapterDescriptor, MappingProfile } from '@hellgato/core/contracts';
import type { SidecarOptions, SidecarAdapter, RuntimeManifest } from '../../../index.js';
import { createSidecarAdapter } from '../../sidecar/index.mjs';
import packageManifest from '../../../package.json' with { type: 'json' };
import { assertMappingProfile } from '@hellgato/core/contracts';

export const MIRABOX_N4_PRO_ADAPTER_ID = 'mirabox-n4-pro';
export const MIRABOX_N4_PRO_MODEL_ID = 'n4-pro';
export const miraboxN4ProDescriptor: Readonly<AdapterDescriptor> = Object.freeze({
  id: MIRABOX_N4_PRO_ADAPTER_ID, version: packageManifest.version, modelIds: [MIRABOX_N4_PRO_MODEL_ID],
  platforms: ['win32'], architectures: ['x64'], apiVersion: 1, protocol: { major: 1, minor: 0 },
});

/** Explicit experimental binding. Runtime bytes and a fresh host-readiness provider are caller-owned. */
export function createMiraboxN4ProAdapter(options: SidecarOptions & { manifest: RuntimeManifest }): SidecarAdapter {
  if (!options?.manifest || !options.manifest.buildId || !options.manifest.executableSha256) {
    throw new TypeError('N4 requires an explicit runtime manifest with build ID and executable SHA-256');
  }
  if (options.manifest.platform !== 'win32' || options.manifest.arch !== 'x64') {
    throw new TypeError('The N4 binding currently targets a verified Windows x64 runtime only');
  }
  const adapter = createSidecarAdapter({ ...options, descriptor: miraboxN4ProDescriptor, expectedBuildId: options.expectedBuildId ?? options.manifest.buildId });
  return adapter;
}

/** Identity is explicit and never derived from the physical USB serial. */
export function createN4ProMappingProfile(options: { virtualSerial: string; childSerial: string; id?: string; macAddress?: number[] }): MappingProfile {
  if (!options || typeof options.virtualSerial !== 'string' || typeof options.childSerial !== 'string') {
    throw new TypeError('N4 mapping requires caller-owned virtualSerial and childSerial');
  }
  return assertMappingProfile({
    id: options.id ?? 'mirabox-n4-pro-ten-key', version: '1.0.0',
    target: {
      id: 'streamdeck-plus-ten-key',
      displays: [{ id: 'lcd', width: 800, height: 480, format: 'jpeg', partial: false }],
      geometry: { rows: 2, columns: 5, keyCount: 10, keyWidth: 120, keyHeight: 120, encoderCount: 4,
        touchWidth: 800, touchHeight: 100, productName: 'Stream Deck +' },
      config: { serialNumber: options.virtualSerial, childSerialNumber: options.childSerial,
        dockFirmwareVersion: '1.01.016', childFirmwareVersion: '2.00.026', productId: 0x0084,
        macAddress: [...(options.macAddress ?? [2, 0, 0, 0, 0, 2])] },
    },
    controls: { keys: Array.from({ length: 10 }, (_, index) => index), dials: [0, 1, 2, 3] },
    touch: { rotation: 0 },
    displays: [...Array.from({ length: 10 }, (_, index) => ({ source: `key:${index}`, target: `key:${index}` })),
      { source: 'touch', target: 'touch' }, { source: 'lcd', target: 'lcd' }],
    requirements: { capabilities: ['keys', 'dials', 'touch', 'dialPress'], hostPreparation: ['streamdeck-plus-ten-key'] },
  });
}

export const miraboxN4ProSupport = Object.freeze({
  status: 'experimental' as const, physicalHardwareVerified: false as const,
  limitations: Object.freeze([
    'The new IPC runtime is hardware-gated and is not the production N4 engine.',
    'Dial clicks are pulses; real dial release, hold and press-and-turn are unavailable.',
    'Windows host preparation is separate and must be verified before opening bridge ports.',
    'Only one active physical device and no genuine Stream Deck Plus coexistence are in scope.',
  ]),
});
