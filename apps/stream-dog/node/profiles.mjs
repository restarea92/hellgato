import { createN4ProProfile } from 'hellgato';

// Preserve the old product's independently persisted dock/child serial and MAC
// identity exactly. This is a compatibility adapter, not the new default SDK API.
export function createLegacyProductProfile(name = 'target', dock, child) {
  if (!['control', 'target'].includes(name)) throw new Error('Profile must be control or target');
  const dockIdentity = dock === undefined ? (name === 'target' ? 2 : 1) : Number(dock);
  const childIdentity = child === undefined ? dockIdentity : Number(child);
  const profile = createN4ProProfile(dockIdentity);
  profile.config.childSerialNumber = createN4ProProfile(childIdentity).config.childSerialNumber;
  if (name === 'control') Object.assign(profile.geometry, { columns: 4, keyCount: 8 });
  return profile;
}
