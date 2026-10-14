import type { Adapter, OperationContext } from '../../index.js';
import type { DeviceDescriptor, MappingProfile, InputEvent, DisplayFrame, ControlCommand } from '@hellgato/core/contracts';
export interface SimulatorOptions {
  device?: DeviceDescriptor;
  deviceId?: string;
  adapterId?: string;
  modelId?: string;
  keys?: number;
  dials?: number;
  touch?: { width: number; height: number } | null;
  keyDimensions?: { width: number; height: number };
}
export type SimulatorInput =
  | { type: 'key'; index: number; state: 'down' | 'up' }
  | { type: 'dial'; kind: 'rotate'; index: number; delta: number }
  | { type: 'dial'; kind: 'press'; index: number; state: 'down' | 'up' }
  | { type: 'dial'; kind: 'pulse'; index: number }
  | { type: 'touch'; gesture: 'tap' | 'hold'; x: number; y: number }
  | { type: 'touch'; gesture: 'swipe'; x: number; y: number; endX: number; endY: number };
export interface SimulatorAdapter extends Adapter {
  readonly device: DeviceDescriptor;
  readonly outputs: DisplayFrame[];
  readonly controls: ControlCommand[];
  readonly calls: string[];
  emitInput(event: SimulatorInput): InputEvent;
  injectInput(event: unknown): void;
  disconnect(): void;
}
export function createSimulator(options?: SimulatorOptions): SimulatorAdapter;
export function createButtonSimulator(options?: SimulatorOptions): SimulatorAdapter;
export function createNonN4Simulator(options?: SimulatorOptions): SimulatorAdapter;
export function createSimulatorProfile(device: DeviceDescriptor, options?: { id?: string; columns?: number; virtualSerial?: string; childSerial?: string }): MappingProfile;
export function replayInputs(events: InputEvent[], sink: (input: InputEvent) => unknown, options?: { signal?: AbortSignal; generation?: number; sessionId?: string; deviceId?: string }): Promise<{ count: number; physicalHardwareVerified: false }>;
export function runAdapterConformance(adapter: Adapter, options: OperationContext & { deviceId: string; output?: DisplayFrame; control?: ControlCommand }): Promise<{ adapterId: string; checks: string[]; physicalHardwareVerified: false }>;
