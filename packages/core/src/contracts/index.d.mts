export type CapabilityStatus = 'native' | 'emulated' | 'unsupported' | 'unverified';
export interface Capability { status: CapabilityStatus; limitations?: string[] }
export interface DisplaySurface { id: string; width: number; height: number; format: 'jpeg' | 'rgb888'; partial: boolean }
export interface DeviceCapabilities {
  keys: number;
  dials: number;
  touch: { width: number; height: number } | null;
  displays: DisplaySurface[];
  dialPress: 'native' | 'pulse' | 'unsupported' | 'unverified';
  dialRelease: 'native' | 'unavailable' | 'unverified';
  features?: Record<string, Capability>;
}
export interface AdapterDescriptor {
  id: string;
  version: string;
  modelIds: string[];
  platforms: string[];
  architectures: string[];
  apiVersion: 1;
  protocol?: { major: number; minor: number };
}
export interface DeviceDescriptor {
  id: string;
  adapterId: string;
  modelId: string;
  firmware?: string;
  displayName?: string;
  capabilities: DeviceCapabilities;
}
export interface Geometry {
  rows: number; columns: number; keyCount: number; keyWidth: number; keyHeight: number;
  encoderCount: number; touchWidth: number; touchHeight: number; productName: string;
}
export interface DeviceConfig {
  serialNumber: string; childSerialNumber: string; dockFirmwareVersion: string;
  childFirmwareVersion: string; productId: number; macAddress: number[];
}
export interface HostTarget { id: string; geometry: Geometry; config: DeviceConfig; displays?: DisplaySurface[] }
export interface MappingProfile {
  id: string; version: string; target: HostTarget;
  controls: { keys: Array<number | null>; dials: Array<number | null> };
  requirements: { hostPreparation?: string[]; capabilities?: string[] };
  touch?: { rotation?: 0 | 90 | 180 | 270 };
  displays?: Array<{ source: string; target: string }>;
}
export interface InputEnvelope { deviceId: string; sessionId?: string; generation: number; sequence: number }
export type InputEvent = InputEnvelope & (
  { type: 'key'; index: number; state: 'down' | 'up' } |
  { type: 'dial'; kind: 'rotate'; index: number; delta: number } |
  { type: 'dial'; kind: 'press'; index: number; state: 'down' | 'up' } |
  { type: 'dial'; kind: 'pulse'; index: number } |
  { type: 'touch'; gesture: 'tap' | 'hold'; x: number; y: number } |
  { type: 'touch'; gesture: 'swipe'; x: number; y: number; endX: number; endY: number }
);
export interface OutputEnvelope { deviceId?: string; sessionId?: string; generation?: number; sequence?: number }
export interface DisplayFrame extends OutputEnvelope {
  type: 'frame'; surfaceId: string; format: 'jpeg' | 'rgb888'; width: number; height: number;
  /** Strict padded base64, at most MAX_FRAME_BYTES decoded bytes. */
  data: string;
  region?: { x: number; y: number; width: number; height: number };
}
export type ControlCommand = OutputEnvelope & { type: 'control'; surfaceId?: string } & (
  { control: 'brightness' | 'sleepDuration'; value: number } |
  { control: 'clear' | 'sleep' | 'wake' | 'showLogo' } |
  { control: 'fill'; color: [number, number, number] }
);
export type OutputEvent = DisplayFrame | ControlCommand;
export const CONTRACT_VERSION: 1;
export const MAX_FRAME_BYTES: number;
export const MAX_DIMENSION: number;
export const MAX_PIXELS: number;
export class ContractError extends TypeError { readonly code: 'ERR_CONTRACT'; readonly path: string; constructor(message: string, path?: string) }
export function validateContract(name: string, value: unknown): unknown;
export function validateAdapterDescriptor(value: unknown): AdapterDescriptor;
export function validateDeviceDescriptor(value: unknown): DeviceDescriptor;
export function validateDeviceCapabilities(value: unknown): DeviceCapabilities;
export function validateHostTarget(value: unknown): HostTarget;
export function validateMappingProfile(value: unknown): MappingProfile;
export function validateInputEvent(value: unknown): InputEvent;
export function validateDisplayFrame(value: unknown): DisplayFrame;
export function validateControlCommand(value: unknown): ControlCommand;
export {
  validateAdapterDescriptor as assertAdapterDescriptor,
  validateDeviceDescriptor as assertDeviceDescriptor,
  validateDeviceCapabilities as assertDeviceCapabilities,
  validateHostTarget as assertHostTarget,
  validateMappingProfile as assertMappingProfile,
  validateInputEvent as assertInputEvent,
  validateDisplayFrame as assertDisplayFrame,
  validateControlCommand as assertControlCommand,
};
