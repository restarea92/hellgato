export function profile(name, identity) {
  if (!['control', 'target'].includes(name)) throw new Error('Profile must be control or target');
  const target = name === 'target';
  const number = identity === undefined ? (target ? 2 : 1) : Number(identity);
  if (!Number.isInteger(number) || number < 1 || number > 999999) throw new Error('Identity must be an integer from 1 to 999999');
  const suffix = String(number).padStart(6, '0');
  return {
    geometry: { rows: 2, columns: target ? 5 : 4, keyCount: target ? 10 : 8,
      keyWidth: 120, keyHeight: 120, encoderCount: 4, touchWidth: 800, touchHeight: 100,
      productName: 'Stream Deck +' },
    config: { dockFirmwareVersion: '1.01.016', childFirmwareVersion: '2.00.026',
      serialNumber: `HGDOCK${suffix}`,
      childSerialNumber: `HGMOCK${suffix}`,
      productId: 0x0084, macAddress: [2, 0, 0, (number >>> 16) & 255, (number >>> 8) & 255, number & 255] }
  };
}
