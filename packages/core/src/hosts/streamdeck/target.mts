import type { EventEmitter } from 'node:events';
import type { Server } from 'node:net';
import type { HostTarget, Geometry, DeviceConfig } from '../../contracts/index.mjs';
import type { Bridge } from '../../bridge.mjs';
/** Narrow compatibility boundary for the pinned JavaScript protocol runtime. */
export interface TransportServer extends EventEmitter {
  server: Server; hasClient: boolean;
  start(): Promise<void>; stop(): Promise<void>;
}
export interface PrimaryTransport extends TransportServer {
  childPort: number | null;
  setDeviceConfig(config: DeviceConfig): void;
}
export interface ChildTransport extends TransportServer {
  sleepSeconds: number; keyStates: Uint8Array; encoderPressMask: number;
  sendKeyEvent(index: number, state: 'down' | 'up'): void;
  sendDial(event: Parameters<Bridge['sendDial']>[0]): void;
  sendTouch(event: Parameters<Bridge['sendTouch']>[0]): void;
}
export type PrimaryConstructor = new (geometry: Geometry, port: number, child: boolean, options: { childPort: number }) => PrimaryTransport;
export type ChildConstructor = new (geometry: Geometry, port: number, config: DeviceConfig, primary: boolean) => ChildTransport;

import { validateHostTarget, ContractError } from '../../contracts/index.mjs';

// These are capabilities of this Stream Deck protocol implementation, never
// universal restrictions on physical DeviceCapabilities.
export function validateStreamDeckTarget(input: unknown): HostTarget {
  const target = validateHostTarget(input);
  const geometry = target.geometry;
  if (geometry.keyCount > geometry.rows * geometry.columns) throw new RangeError('Keys exceed the grid');
  if (geometry.encoderCount > 4) throw new RangeError('Stream Deck target supports at most four encoders');
  if (Boolean(geometry.touchWidth) !== Boolean(geometry.touchHeight)) throw new ContractError('touch dimensions must both be zero or nonzero', '$.target.geometry');
  if (geometry.touchWidth && (geometry.touchWidth !== 800 || geometry.touchHeight !== 100)) throw new RangeError('Stream Deck Plus touch target must be 800x100; physical touch sensors are mapped by the adapter');
  return target;
}

export async function createStreamDeckTransport(target: HostTarget, { port, childPort, sleepDuration }: { port: number; childPort: number; sleepDuration: number }): Promise<{ primary: PrimaryTransport; child: ChildTransport }> {
  const [{ ElgatoServer }, { ElgatoChildServer }, { withDisplayCommands }, { withFirmwarePolicy }] = await Promise.all([
    import('../../vendor/cora/primary-server.js'),
    import('../../vendor/cora/child-server.js'),
    import('./display-server.mjs'),
    import('./firmware-policy.mjs'),
  ]);
  const PrimaryServer = withFirmwarePolicy(ElgatoServer, 'primary');
  const primary = new PrimaryServer(target.geometry, port, true, { childPort });
  primary.setDeviceConfig(target.config);
  const DisplayServer = withDisplayCommands(ElgatoChildServer);
  const child = new DisplayServer(target.geometry, childPort, target.config, false);
  child.sleepSeconds = sleepDuration;
  return { primary, child };
}
