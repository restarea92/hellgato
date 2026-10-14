// Retained root exports for existing consumers. New sessions never use legacy N4 defaults.
export { createBridge, createN4ProProfile, createDeviceBridge } from '@hellgato/core';
export { createSession, SessionError } from './src/session/index.mjs';
export { createSidecarAdapter } from './src/sidecar/index.mjs';
