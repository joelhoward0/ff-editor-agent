// Triage view vs the OFFICIAL MCP Apps host bridge (see run.mjs).
//   node triage.mjs [triage_app.html]
// Exits non-zero unless: handshake completes, the app sizes itself, decisions,
// a note and a quoted selection arrive as one valid ui/message, and an update
// is sent as a replacement.
import { chromium } from 'playwright';
import fs from 'fs';

const htmlPath = process.argv[2] || new URL('../../triage_app.html', import.meta.url).pathname;
const app = fs.readFileSync(htmlPath, 'utf8');
const bundle = fs.readFileSync(new URL('./host.js', import.meta.url), 'utf8');
const args = {
  scenes: [
    'The lamp flame stands up straight. Jamie has been staring at it for an hour.',
    'There was a vote. He sat at that table and voted with everybody else, and she voted first.',
    'Down the corridor the generator coughs, dies, catches.',
  ],
  titles: ['The lamp', 'The vote', ''],
  context: 'Ch 38 triage test',
};

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 840, height: 700 } });
await p.setContent('<!doctype html><body style="margin:0"></body>');
await p.addScriptTag({ content: bundle });
await p.evaluate(([h, a]) => window.runHost(h, a), [app, args]);
await p.waitForTimeout(1500);

const events = () => p.evaluate(() => window.events);
const messages = async () => (await events()).filter((e) => e[0] === 'message').map((e) => e[1].content);
const height = await p.evaluate(() => document.querySelector('iframe').getBoundingClientRect().height);
const ok = { initialized: (await events()).some((e) => e[0] === 'initialized'), sized: height > 300 };

if (ok.initialized) {
  const fr = p.frameLocator('iframe');
  const scene = (i) => fr.locator('.scene').nth(i);
  await scene(0).getByRole('button', { name: 'Keep' }).click();
  await scene(1).getByRole('button', { name: 'Fix story' }).click();
  await scene(1).locator('textarea').fill('she would never vote first');
  // select "she voted first" inside scene 2's prose, then quote it
  await scene(1).locator('.prose').evaluate((el) => {
    const t = el.querySelector('p').firstChild; const i = t.textContent.indexOf('she voted first');
    const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + 'she voted first'.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  });
  await scene(1).getByRole('button', { name: 'Quote selected text' }).click();
  ok.nothingSentYet = (await messages()).length === 0;
  await fr.getByRole('button', { name: 'Send triage' }).click();
  await p.waitForTimeout(400);
  await scene(2).getByRole('button', { name: 'Cut' }).click();
  await fr.getByRole('button', { name: 'Send updated triage' }).click();
  await p.waitForTimeout(400);
  const [first, second] = (await messages()).map((c) => Array.isArray(c) && c[0].text);
  ok.first = !!first && first.startsWith('[prose-forge triage]')
    && first.includes('Scene 1 (The lamp): KEEP')
    && first.includes('Scene 2 (The vote): FIX STORY — she would never vote first')
    && first.includes('> "she voted first"') && first.includes('(Unmarked: 3)');
  ok.updated = !!second && second.startsWith('[prose-forge triage] Updated') && second.includes('Scene 3: CUT');
  await p.screenshot({ path: '/tmp/triage.png' });
  if (!ok.first || !ok.updated) console.log('MESSAGES', JSON.stringify([first, second]));
}
await b.close();
console.log('TRIAGE', JSON.stringify(ok), 'iframe height', height);
if (!Object.values(ok).every(Boolean)) process.exit(1);
