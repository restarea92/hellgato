import { performance } from 'node:perf_hooks';

/** Preserve the existing host wake windows while fencing frames assembled before wake. */
export class FullFrameScreensaver {
  #now; #epoch = 0; #active = false; #ignoreUntil = 0; #clearUntil = 0;
  constructor({ now = () => performance.now() } = {}) { this.#now = now; }
  get epoch() { return this.#epoch; }
  get active() { return this.#active; }
  accepts(epoch) { return epoch === this.#epoch && this.#now() >= this.#ignoreUntil; }
  enter(epoch) {
    if (!this.accepts(epoch)) return false;
    this.#active = true;
    return true;
  }
  wake() {
    this.#epoch++;
    this.#active = false;
    // Stream Deck can finish an in-flight saver frame and then send a black clear.
    // These windows match the established N4 host handling; neither clears cached surfaces.
    this.#ignoreUntil = this.#now() + 300;
    this.#clearUntil = this.#now() + 1000;
  }
  consumeWakeClear(color) {
    if (!color.every(value => value === 0) || !(this.#active || this.#now() < this.#clearUntil)) return false;
    if (this.#active) this.wake();
    this.#clearUntil = 0;
    return true;
  }
}
