/** Typed boundary for the retained JavaScript compatibility API. */
import type { Bridge, LegacyProfile, LegacyBridgeOptions } from '../bridge.mjs';
export function createN4ProProfile(identity?: number, dockIdentity?: number): LegacyProfile;
export function createBridge(options?: LegacyBridgeOptions): Promise<Bridge>;
