import {writeFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
const [port, url, output, primaryView] = process.argv.slice(2);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let seq = 0; const pending = new Map();
ws.onmessage = event => { const message = JSON.parse(event.data); if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); } };
const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, m => m.error ? reject(Error(m.error.message)) : resolve(m.result)); ws.send(JSON.stringify({id, method, params})); });
await send('Page.enable'); await send('Runtime.enable'); await send('Page.navigate', {url});
let metadata;
for (let i = 0; i < 600; i++) { await delay(100); const value = await send('Runtime.evaluate', {expression: 'window.godotReviewReady || null', returnByValue: true}); if (value.result.value) { metadata = value.result.value; break; } }
if (!metadata) throw Error('Godot scene did not signal readiness');
await delay(500);
if (primaryView) {
  for (const view of [primaryView, ...['front', 'three-quarter', 'side'].filter(v => v !== primaryView)]) {
    const expression = `window.godotReviewView = null; window.godotReviewSetView(${JSON.stringify(view)});`;
    await send('Runtime.evaluate', {expression});
    let state;
    for (let i = 0; i < 100; i++) {
      await delay(100);
      const result = await send('Runtime.evaluate', {expression: 'window.godotReviewView || null', returnByValue: true});
      if (result.result.value?.view === view) { state = result.result.value; break; }
    }
    if (!state) throw Error(`Godot did not acknowledge view ${view}`);
    const image = view === primaryView ? output : join(dirname(output), `capture-${view}.png`);
    const screenshot = await send('Page.captureScreenshot', {format: 'png'});
    await writeFile(image, Buffer.from(screenshot.data, 'base64'));
    await writeFile(image + '.view.json', JSON.stringify(state));
  }
} else {
  const screenshot = await send('Page.captureScreenshot', {format: 'png'});
  await writeFile(output, Buffer.from(screenshot.data, 'base64'));
}
await writeFile(output + '.json', JSON.stringify(metadata));
ws.close();
