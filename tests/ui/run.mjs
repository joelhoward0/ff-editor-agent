// Picker vs the OFFICIAL MCP Apps host bridge (@modelcontextprotocol/ext-apps
// AppBridge), which schema-validates every message the way Claude's host does.
//   cd tests/ui && npm install && npm test        (or: node run.mjs <app.html>)
// Exits non-zero unless the handshake completes, the app sizes itself, and a
// pick arrives as a valid ui/message, and a changed pick is sent as a correction.
import { chromium } from 'playwright';
import fs from 'fs';

const htmlPath = process.argv[2] || new URL('../../compare_app.html', import.meta.url).pathname;
const app = fs.readFileSync(htmlPath, 'utf8');
const bundle = fs.readFileSync(new URL('./host.js', import.meta.url), 'utf8');
const args = {
  passages: ['There was a vote. He sat at that table.', 'There was a vote, and he sat at that table and voted, but she voted first.', 'A vote. She voted first.'],
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
const ok = { initialized: (await events()).some((e) => e[0] === 'initialized'), sized: height > 200 };

if (ok.initialized) {
  const fr = p.frameLocator('iframe');
  const messages = async () => (await events()).filter((e) => e[0] === 'message').map((e) => e[1]);
  const valid = (m) => Array.isArray(m.content) && m.content[0].text.startsWith('[prose-forge pick]');
  await fr.getByRole('button', { name: 'Pick C' }).click();
  await p.waitForTimeout(300);
  ok.selectDoesNotSend = (await messages()).length === 0;
  await fr.getByRole('button', { name: 'Send pick' }).click();
  await p.waitForTimeout(300);
  // change of mind: edit B, pick it, send again
  await fr.locator('textarea').nth(1).fill('There was a vote, and he sat there and voted, but she voted first.');
  await fr.getByRole('button', { name: 'Pick B' }).click();
  await fr.getByRole('button', { name: 'Send changed pick' }).click();
  await p.waitForTimeout(500);
  const [first, second] = await messages();
  ok.message = !!first && valid(first) && first.content[0].text.includes('Ch 38 hotspot: I chose C');
  ok.changedMind = !!second && valid(second) && /Changed my mind[\s\S]*I chose B \(with my edits\)/.test(second.content[0].text);
  await p.screenshot({ path: '/tmp/picker.png' });
}

// Several short spots in one picker: each picked on its own, sent once, and
// the message names every spot so the pick can't be mistaken for another.
const spotArgs = { context: 'Ch 38 scene 1', spots: [
  { context: 'wine line', passages: ['a glass of Evan\'s wine', 'a very good year'] },
  { context: 'pocket line', passages: ['warm against his ribs', 'body heat', 'a lump in his pocket'] },
] };
const p2 = await b.newPage({ viewport: { width: 840, height: 900 } });
await p2.setContent('<!doctype html><body style="margin:0"></body>');
await p2.addScriptTag({ content: bundle });
await p2.evaluate(([h, a]) => window.runHost(h, a), [app, spotArgs]);
await p2.waitForTimeout(1500);
const fr2 = p2.frameLocator('iframe');
await fr2.getByRole('button', { name: 'Pick A for spot 1' }).click();
ok.waitsForAllSpots = await fr2.getByRole('button', { name: 'Send picks' }).isDisabled();
await fr2.getByRole('button', { name: 'None of these for spot 2' }).click();
await fr2.getByRole('button', { name: 'Send picks' }).click();
await p2.waitForTimeout(500);
const spotMsg = (await p2.evaluate(() => window.events)).filter((e) => e[0] === 'message').map((e) => e[1].content[0].text)[0] || '';
ok.spots = spotMsg.startsWith('[prose-forge pick] Ch 38 scene 1\n1. wine line: I chose A over B.\n2. pocket line: None of these');
await p2.screenshot({ path: '/tmp/picker-spots.png' });
await b.close();
console.log('SPOT MESSAGE', JSON.stringify(spotMsg));
console.log('RESULT', JSON.stringify(ok), 'iframe height', height);
if (!(ok.initialized && ok.sized && ok.selectDoesNotSend && ok.message && ok.changedMind && ok.waitsForAllSpots && ok.spots)) {
  console.log('HOST CONSOLE', JSON.stringify(consoleLines));
  process.exit(1);
}
