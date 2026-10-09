// Picker vs the OFFICIAL MCP Apps host bridge (@modelcontextprotocol/ext-apps
// AppBridge), which schema-validates every message the way Claude's host does.
//   cd tests/ui && npm install && npm test        (or: node run.mjs <app.html>)
// Exits non-zero unless the handshake completes, the app sizes itself, and a
// pick arrives as a valid ui/message.
import { chromium } from 'playwright';
import fs from 'fs';

const htmlPath = process.argv[2] || new URL('../../compare_app.html', import.meta.url).pathname;
const app = fs.readFileSync(htmlPath, 'utf8');
const bundle = fs.readFileSync(new URL('./host.js', import.meta.url), 'utf8');
const args = {
  passages: ['There was a vote. He sat at that table.', 'There was a vote, and he sat at that table and voted, but she voted first.'],
  labels: [], context: 'Ch 38 hotspot',
};

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 840, height: 700 } });
const consoleLines = [];
p.on('console', (m) => consoleLines.push(m.text()));
await p.setContent('<!doctype html><body style="margin:0"></body>');
await p.addScriptTag({ content: bundle });
await p.evaluate(([h, a]) => window.runHost(h, a), [app, args]);
await p.waitForTimeout(1500);

const events = () => p.evaluate(() => window.events);
const height = await p.evaluate(() => document.querySelector('iframe').getBoundingClientRect().height);
const ok = { initialized: (await events()).some((e) => e[0] === 'initialized'), sized: height > 200, message: false };

if (ok.initialized) {
  const fr = p.frameLocator('iframe');
  await fr.locator('textarea').nth(1).fill('There was a vote, and he sat there and voted, but she voted first.');
  await fr.getByRole('button', { name: 'Pick B' }).click();
  await p.waitForTimeout(500);
  const msg = (await events()).find((e) => e[0] === 'message');
  ok.message = !!(msg && Array.isArray(msg[1].content) && msg[1].content[0].text.startsWith('[prose-forge pick]'));
  await p.screenshot({ path: '/tmp/picker.png' });
}
await b.close();
console.log('RESULT', JSON.stringify(ok), 'iframe height', height);
if (!(ok.initialized && ok.sized && ok.message)) {
  console.log('HOST CONSOLE', JSON.stringify(consoleLines));
  process.exit(1);
}
