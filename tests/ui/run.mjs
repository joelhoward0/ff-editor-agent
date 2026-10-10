// Picker vs the OFFICIAL MCP Apps host bridge (@modelcontextprotocol/ext-apps
// AppBridge), which schema-validates every message the way Claude's host does.
//   cd tests/ui && npm install && npm test        (or: node run.mjs <app.html>)
// Exits non-zero unless the handshake completes, the app sizes itself, and a
// pick arrives as a valid ui/message, and a changed pick is sent as a correction.
// Runs twice: a host without record_pick (Claude records), and one with it (saved).
import { chromium } from 'playwright';
import fs from 'fs';

const htmlPath = process.argv[2] || new URL('../../compare_app.html', import.meta.url).pathname;
const app = fs.readFileSync(htmlPath, 'utf8');
const bundle = fs.readFileSync(new URL('./host.js', import.meta.url), 'utf8');
const args = {
  passages: ['There was a vote. He sat at that table.', 'There was a vote, and he sat at that table and voted, but she voted first.', 'A vote. She voted first.'],
  labels: [], context: 'Ch 38 hotspot',
};

async function runOnce(saving) {
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 840, height: 700 } });
const consoleLines = [];
p.on('console', (m) => consoleLines.push(m.text()));
await p.setContent('<!doctype html><body style="margin:0"></body>');
await p.addScriptTag({ content: bundle });
await p.evaluate(([h, a, sv]) => window.runHost(h, a, sv), [app, args, saving]);
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
  ok.message = !!first && valid(first) && first.content[0].text.includes('I chose C');
  ok.changedMind = !!second && valid(second) && /Changed my mind[\s\S]*I chose B \(with my edits\)/.test(second.content[0].text);
  const savedNote = (m) => m.content[0].text.endsWith('(Saved to your voice ledger.)');
  const calls = (await events()).filter((e) => e[0] === 'toolcall').map((e) => e[1]);
  if (saving) {
    // both sends saved under ONE pick id, the second with the edited text
    ok.saved = calls.length === 2 && calls.every((c) => c.name === 'record_pick')
      && calls[0].arguments.pick_id === calls[1].arguments.pick_id
      && calls[0].arguments.chosen === 2 && calls[1].arguments.chosen === 1
      && calls[1].arguments.chosen_text.startsWith('There was a vote, and he sat there')
      && calls[1].arguments.passages[1] === args.passages[1]
      && savedNote(first) && savedNote(second);
  } else {
    ok.unsavedFallback = calls.length === 0 && !savedNote(first) && !savedNote(second);
  }
  await p.screenshot({ path: '/tmp/picker.png' });
}
await b.close();
console.log(saving ? 'SAVING' : 'RESULT', JSON.stringify(ok), 'iframe height', height);
if (!Object.values(ok).every(Boolean)) {
  console.log('HOST CONSOLE', JSON.stringify(consoleLines));
  return false;
}
return true;
}

const results = [await runOnce(false), await runOnce(true)];
if (!results.every(Boolean)) process.exit(1);
