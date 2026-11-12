import { EventEmitter } from 'node:events';

export class FrameSessionSupervisor extends EventEmitter {
  #start; #desired = false; #session; #task; #abort; #retry; #state = 'stopped'; #attempt = 0; #error = null;
  constructor({ start, retryMs = attempt => Math.min(10000, 500 * 2 ** Math.min(attempt - 1, 5)) }) {
    super(); this.#start = start; this.#retry = retryMs;
  }
  status() { return { state: this.#state, desired: this.#desired, attempt: this.#attempt, error: this.#error }; }
  #publish(state, error = null) { this.#state = state; this.#error = error; this.emit('state', this.status()); }
  connect() {
    this.#desired = true;
    if (this.#task) return;
    this.#abort = new AbortController();
    const signal = this.#abort.signal;
    this.#task = this.#run(signal).finally(() => {
      this.#task = null; this.#session = null; this.#publish('stopped');
      if (this.#desired) this.connect();
    });
  }
  async #run(signal) {
    while (this.#desired && !signal.aborted) {
      this.#attempt++;
      this.#publish('connecting');
      try {
        const session = await this.#start({ signal, generation: this.#attempt });
        this.#session = session;
        if (!this.#desired || signal.aborted) { await session.stop(); await session.completion.catch(() => {}); break; }
        this.#publish('running');
        await session.completion;
        if (this.#desired && !signal.aborted) throw new Error('Device session ended');
      } catch (error) {
        if (!this.#desired || signal.aborted) break;
        this.#publish('waiting', error.message);
        await new Promise(resolveWait => {
          const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolveWait(); };
          const timer = setTimeout(done, this.#retry(this.#attempt));
          signal.addEventListener('abort', done, { once: true });
          if (signal.aborted) done();
        });
      } finally { this.#session = null; }
    }
  }
  async disconnect() {
    this.#desired = false;
    this.#abort?.abort();
    this.#publish('stopping');
    await this.#session?.stop();
    await this.#task;
    if (!this.#desired) this.#publish('stopped');
  }
  async control(command) {
    if (!this.#session || this.#state !== 'running') throw new Error('Device is not connected');
    return this.#session.control(command);
  }
  async configure(settings) {
    if (!this.#session || this.#state !== 'running' || typeof this.#session.configure !== 'function') return false;
    await this.#session.configure(settings); return true;
  }
}
