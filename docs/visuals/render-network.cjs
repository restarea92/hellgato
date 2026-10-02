const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const html = path.resolve(process.argv[2] || path.join(__dirname, 'hellgato-network.html'));
const output = path.resolve(process.argv[3] || path.join(__dirname, '../../work/hellgato-network-frames'));
const targetFps = 24;

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'msedge', args: ['--enable-unsafe-swiftshader'] });
  try {
    const page = await browser.newPage({ viewport: { width: 832, height: 1100 }, colorScheme: 'light', reducedMotion: 'reduce', deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(html).href);
    const frame = page.frames().find(candidate => candidate !== page.mainFrame()) || page.mainFrame();
    const root = frame.locator('#hellgato-pixel-network');
    await frame.locator('#hellgato-pixel-network[data-ready="true"],#hellgato-pixel-network[data-error]').waitFor({ timeout: 45000 });
    await frame.addStyleTag({ content: `
      html,body { background:#fff !important; color-scheme:light; }
      #hellgato-pixel-network { position:relative; width:800px; height:800px; background:#fff; }
      #hellgato-pixel-network h2,#hellgato-pixel-network .viz-controls,
      #hellgato-pixel-network .px-foot,#hellgato-pixel-network .px-mobile-flow { display:none; }
      #hellgato-pixel-network .px-legend { position:absolute; top:18px; left:0; width:100%; margin:0; justify-content:center; z-index:2; }
    ` });
    await frame.evaluate(() => document.fonts.ready);
    await root.evaluate(element => {
      for (const selector of ['[data-labels]', '[data-info]']) {
        const button = element.querySelector(selector);
        if (button.getAttribute('aria-pressed') === 'false') button.click();
      }
      element.querySelector('[data-flow="both"]').click();
    });
    await page.waitForTimeout(100);
    const state = await root.evaluate(element => ({ ...element.dataset }));
    if (state.error) throw Error(state.error);
    if (state.packets !== '80') throw Error('Expected 40 packets per direction');
    const period = Number(state.loopPeriodMs);
    const count = Math.round(period * targetFps / 1000);
    if (count < 1 || period % 10 !== 0) throw Error('Loop period must fit GIF centisecond timing');
    // GIF delays use 10 ms units. Sample at the encoded timestamps so motion
    // keeps a constant speed even when adjacent delays alternate 40/50 ms.
    const timestamps = Array.from({ length: count + 1 }, (_, index) => Math.round(index * period / count / 10) * 10);
    const frameDurations = timestamps.slice(1).map((time, index) => time - timestamps[index]);
    if (frameDurations.some(duration => duration < 20)) throw Error('Frame delay is too short for reliable GIF playback');
    let first;
    for (let index = 0; index <= count; index++) {
      await root.evaluate((element, time) => element.renderAt(time), timestamps[index]);
      const file = index === count ? 'loop-end.png' : `frame-${String(index).padStart(3, '0')}.png`;
      const screenshot = await root.screenshot({ path: path.join(output, file), animations: 'disabled' });
      if (index === 0) first = screenshot;
      if (index === count && !first.equals(screenshot)) throw Error('Loop endpoint differs from the first frame');
    }
    if (errors.length) throw Error(errors.join('\n'));
    const settings = { width: 800, height: 800, targetFps, averageFps: count * 1000 / period, frameDurations, frames: count, period, cycleMs: Number(state.cycleMs), packets: Number(state.packets), background: '#ffffff', loopEndpointMatches: true };
    await fs.writeFile(path.join(output, 'capture.json'), JSON.stringify(settings, null, 2) + '\n');
    console.log(JSON.stringify({ ...settings, output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error);process.exitCode = 1; });
