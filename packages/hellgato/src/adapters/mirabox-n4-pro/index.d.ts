import type { AdapterDescriptor, MappingProfile } from '@hellgato/core/contracts';
import type { SidecarOptions, SidecarAdapter, RuntimeManifest } from '../../../index.js';
export const MIRABOX_N4_PRO_ADAPTER_ID: 'mirabox-n4-pro';
export const MIRABOX_N4_PRO_MODEL_ID: 'n4-pro';
export const miraboxN4ProDescriptor: Readonly<AdapterDescriptor>;
export function createMiraboxN4ProAdapter(options: SidecarOptions & { manifest: RuntimeManifest }): SidecarAdapter;
export function createN4ProMappingProfile(options: { virtualSerial: string; childSerial: string; id?: string; macAddress?: number[] }): MappingProfile;
export const miraboxN4ProSupport: Readonly<{ status: 'experimental'; physicalHardwareVerified: false; limitations: readonly string[] }>;
