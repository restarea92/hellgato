export function withDisplayCommands(ChildServer) {
  return class DisplayServer extends ChildServer {
    handleSendReport(payload, flags, messageId) {
      if (payload[0] !== 3) return super.handleSendReport(payload, flags, messageId);
      if (payload[1] === 8 && payload.length >= 3 && payload[2] <= 100) {
        this.emit('brightness', payload[2]);
      } else if (![2, 5, 0x0d].includes(payload[1])) {
        return;
      }
      if (flags & 0x4000) this.sendAckNak(messageId);
    }
  };
}
