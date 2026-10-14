const GET_REPORT = 2;
const SEND_REPORT = 1;
const VERBATIM = 0x8000;
const PRIMARY_QUERIES = new Set([0x05, 0x06, 0x08, 0x0b, 0x1a, 0x1c, 0x80, 0x83, 0x84, 0x85, 0x87, 0x8f]);
const CHILD_QUERIES = new Set([0x1a, 0x1c, 0x80]);
const DISPLAY_CONTROLS = new Set([2, 5, 6, 8, 0x0d]);
const IMAGE_COMMANDS = new Set([1, 7, 8, 0x0b, 0x0c]);

export function unsupportedWrite(role, flags, hidOp, payload) {
  if (!payload.length || hidOp === GET_REPORT) return false;
  const [report, command] = payload;
  if (role === 'primary') {
    if (hidOp === SEND_REPORT) return report !== 3 || command !== 0x1a;
    if (report === 3 && PRIMARY_QUERIES.has(command)) return false;
    // Non-verbatim dock probes with no HID operation are legacy read requests.
    return report === 2 || Boolean(flags & VERBATIM);
  }
  if (report === 3) return !DISPLAY_CONTROLS.has(command) && !CHILD_QUERIES.has(command);
  if (report === 2 && hidOp !== SEND_REPORT) return !IMAGE_COMMANDS.has(command);
  if (payload.length >= 6 && report === 5 && command === 0x55 &&
      payload[2] === 0xaa && payload[3] === 0xd1 && payload[5] <= 100) return false;
  return hidOp === SEND_REPORT || Boolean(flags & VERBATIM);
}

export function withFirmwarePolicy(Server, role = 'child') {
  return class FirmwareSafeServer extends Server {
    handleCoraPacket(flags, hidOp, messageId, payload) {
      if (!unsupportedWrite(role, flags, hidOp, payload)) {
        return super.handleCoraPacket(flags, hidOp, messageId, payload);
      }
      const detail = { reason: 'unsupported', role, hidOp, messageId,
        reportId: payload[0], command: payload[1] };
      this.emit('operationRejected', detail);
      this.emitLog?.('warn', `unsupported ${role} write blocked (report=${payload[0]} command=${payload[1]} hidOp=${hidOp})`);
      // CORA's available AckNak encoder only acknowledges success. Closing rejects
      // pending requests without inventing a firmware result or forwarding bytes.
      this.client?.destroy();
    }
  };
}
