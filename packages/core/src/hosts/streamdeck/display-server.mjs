import { withFirmwarePolicy } from './firmware-policy.mjs';

export function withDisplayCommands(ChildServer) {
  return class DisplayServer extends withFirmwarePolicy(ChildServer) {
    sleepSeconds = 0;
    lcdAssembly = null;

    onClientConnected(socket) {
      this.lcdAssembly = null;
      super.onClientConnected(socket);
    }

    handleGetReport(reportId, messageId, payload) {
      if (reportId !== 0x0a) return super.handleGetReport(reportId, messageId, payload);
      const report = Buffer.alloc(32);
      report.set([0x0a, 4]);
      report.writeInt32LE(this.sleepSeconds, 2);
      this.sendFrame(report, 0x8100, 0, messageId);
    }

    handleSendReport(payload, flags, messageId) {
      if (payload[0] !== 3) return super.handleSendReport(payload, flags, messageId);
      switch (payload[1]) {
        case 2:
          this.emit('displayCommand', { type: 'showLogo' });
          break;
        case 5:
          if (payload.length < 5) return;
          this.emit('displayCommand', { type: 'fill', color: [...payload.subarray(2, 5)] });
          break;
        case 6:
          if (payload.length < 6 || payload[2] >= this.childGeometry.keyCount) return;
          this.emit('displayCommand', { type: 'fill', keyIndex: payload[2], color: [...payload.subarray(3, 6)] });
          break;
        case 8:
          if (payload.length < 3 || payload[2] > 100) return;
          this.emit('brightness', payload[2]);
          break;
        case 0x0d:
          if (payload.length < 6 || payload.readInt32LE(2) < 0) return;
          this.sleepSeconds = payload.readInt32LE(2);
          this.emit('sleepDuration', this.sleepSeconds);
          break;
        default:
          return;
      }
      if (flags & 0x4000) this.sendAckNak(messageId);
    }

    handleOutputReport(command, flags, hidOp, messageId, payload) {
      if (command !== 8) return super.handleOutputReport(command, flags, hidOp, messageId, payload);
      const image = this.acceptLcdChunk(payload);
      if (flags & 0x4000) this.sendAckNak(messageId, hidOp);
      if (image) this.emit('lcdImage', { data: image, format: 'jpeg' });
    }

    acceptLcdChunk(payload) {
      if (payload.length < 8) { this.lcdAssembly = null; return null; }
      const size = payload.readUInt16LE(4);
      const index = payload.readUInt16LE(6);
      const last = payload[3] === 1;
      if (payload[3] > 1 || size > payload.length - 8 || (!last && size === 0)) {
        this.lcdAssembly = null;
        return null;
      }
      if (index === 0) this.lcdAssembly = { chunks: [], bytes: 0, next: 0 };
      const assembly = this.lcdAssembly;
      if (!assembly || index !== assembly.next || assembly.bytes + size > 1024 * 1024 || index >= 4096) {
        this.lcdAssembly = null;
        return null;
      }
      if (index === 0 && size > 0) this.emit('lcdFrameStarted');
      assembly.chunks.push(Buffer.from(payload.subarray(8, 8 + size)));
      assembly.bytes += size;
      assembly.next++;
      if (!last) return null;
      this.lcdAssembly = null;
      if (assembly.bytes === 0) return null;
      return Buffer.concat(assembly.chunks, assembly.bytes);
    }
  };
}
