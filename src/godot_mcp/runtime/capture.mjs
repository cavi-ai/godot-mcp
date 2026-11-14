import {writeFile} from 'node:fs/promises';
const [port, url, output] = process.argv.slice(2);
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
const screenshot = await send('Page.captureScreenshot', {format: 'png'});
await writeFile(output, Buffer.from(screenshot.data, 'base64'));
await writeFile(output + '.json', JSON.stringify(metadata));
ws.close();
