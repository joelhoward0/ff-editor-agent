---
name: write-in-my-voice
description: Draft or revise fiction (chapters, scenes, story prose) in the author's own voice, gated by a personal voice detector, AI-tell checks and name continuity with earlier chapters. Use whenever the user asks to write, draft, continue, revise or polish a chapter, scene or story passage, or to set up their writing style, style profile, voice card or series bible.
---

# Write in my voice

What actually moves a draft toward the author's voice (tested blind against a
real author's chapters): **their own recent prose and an accurate voice card
in context before drafting.** Style guides describe what an author aspires
to; Claude follows them into polished literary prose the author doesn't
write. Fixing numbers afterwards barely helps. So: load the real voice first,
draft, then use the prose-forge tools as a gate.

## Where the voice files live

Everything goes in one folder, `prose-forge`, so the author owns it and can
open, edit or delete any of it: `voice-card.md`, `style-profile.json`,
`voice-ledger.md`, `samples.md`, `controls.md`, optional `banlist.txt`.

- **claude.ai / desktop:** a `prose-forge` folder in their Google Drive (find
  it by title with the Drive connector; create it if missing). The `.md`
  files are **Google Docs** titled without the extension (`voice-card`,
  `voice-ledger`, `samples`, `controls`, `banlist`), so the author can read
  and edit them like any doc:
  - Create one with Drive `create_file` from markdown text (it converts to a
    Doc). Read it with Drive `read_file_content`.
  - Change it **in place** with the Google Docs connector (`update_doc`; follow
    the google-workspace skill: read for the revision first, append at the
    body end index − 1). Never recreate a Doc to change it: the link must stay.
  - `style-profile.json` stays a plain file (`disableConversionToGoogleType:
    true`, `application/json`); a Doc would mangle the JSON. It changes only on
    a rebuild: create the new one, then trash the old. If two exist, the
    newest wins.
  - No Docs connector: ask them to turn it on. Until then, change a Doc by
    creating a new one and trashing the old, and say the link changed. No Drive
    connector: ask them to turn it on, or to attach the files.
- **Claude Code:** the project root (where `style-profile.json` also switches
  on the automatic check hook).

## Large files

A manuscript doc can be too big to read at once (a Drive read comes back cut
off, or a Docs `read_doc` result is saved to a file instead of shown), and a
whole chapter is costly to paste into every tool call. Don't page through it
in chat or retype it:

1. **Get it on disk.** If a read was saved to a file, use that path; where
   you can run code, a large Docs `read_doc` saves itself. Without code
   execution, ask which chapters you need and read only those, or ask the
   author to attach the doc.
2. **Split it:** `python <this skill's folder>/scripts/chunk.py split SRC OUTDIR`
   writes one file per chapter (cut at scene breaks past 6,000 words) and
   `index.json` with each file's title, word count and first line. Choose
   chunks from the index, not by reading everything.
3. **Fan out to subagents** when the job means reading many chunks: telling
   the author's chapters from a co-author's, beat summaries for controls,
   gathering samples. Give each subagent file paths for one chunk or a few;
   it writes results to files and returns only short findings (title, POV,
   words, verdict, output path), never the chunk text. Keep blind work blind:
   a controls subagent gets the beat summary only, never the chapter.
4. **Feed tools from files.** Build `samples.md` and `controls.md` by joining
   chunk files on disk, and let a subagent make the `build_style_profile`
   call so the samples never pass through your context. For
   `check_continuity`, pass `chunk.py names SRC OUT` extracts of the draft
   and each canon source (every proper name with the sentence it first
   appears in) instead of the full text. Each text a tool takes is capped at
   400,000 characters; split anything larger.
5. Never rebuild a file's contents from memory or from a cut-off read.

## One-time setup (no style-profile.json yet)

1. **Collect samples:** 15,000–30,000 words the author wrote themselves, never
   AI drafts. Look in Google Drive first (their manuscript docs; ask which
   chapters are fully theirs, and in co-written work which POV/chapters are
   theirs), else ask them to paste or attach. A manuscript too big to read at
   once: see Large files.
2. **Write the voice card.** Read 3 sample chapters and follow
   `voice-card-prompt.md` in this skill's folder. Save as `voice-card.md`.
   Show it to the author and fix anything they say is wrong.
3. **Write controls.** Pick 3 *other* sample chapters (2–5k words each). For
   each, write a flat beat summary (no wording from the chapter), then, from
   the summary and the author's style guide only (NOT the voice card, NOT the
   chapter), draft that chapter as well as you can. Use a fresh subagent per
   chapter if available so you can't see the original. These are Claude's
   natural imitation; the profile learns what gives it away.
4. Call `build_style_profile(samples, controls)` with the step-1 text (minus the
   3 control chapters) and the 3 control drafts. Save `profile` as
   `style-profile.json`. Show the author `imitation_tells`; discard the
   control drafts.
5. Save the step-4 samples text as `samples.md` and the control drafts as
   `controls.md`: retraining needs them (a profile alone can't be rebuilt).
6. Optional: phrases they personally hate, one per line, in `banlist.txt`.

## Every draft

1. **Load the real voice.** First learn from any drafts the author has edited
   since (see Learning from their edits). Then `voice-card.md`, plus the last ~2,000 words of the
   author's own most recent chapter (their prose, not a Claude draft). Then the
   canon: series bible, character notes, the previous chapter or two.
2. **Draft** from the brief, matching the voice card and the excerpt over any
   style guide. Write freely; don't hold banned phrases in mind.
3. **Check:** `check_draft(text, profile, extra_banlist)`.
   - `spans`: rewrite only those sentences.
   - `voice.drift`: habits to fix, not numbers to hit. "More 'doesn't'" means
     narrating what characters don't do; "fewer dashes" means clauses that
     should interrupt or turn were chopped into separate sentences. Work from
     `voice.hotspot` outward, re-reading the voice card as you go. Never
     sprinkle or delete words to move a number. The verdict deliberately
     ignores the features listed in `drift`, so gaming them can't pass.
4. Re-check once. If `voice.verdict` is still not "reads like the author",
   don't loop: hand back with the verdict and the hotspot so the author knows
   where to look.
5. **Continuity:** `check_continuity(draft, canon)`. Fix `near_misses`; list
   `new_names` for the author to confirm. Long canon: pass `chunk.py names`
   extracts (see Large files).
6. **Hand back** the chapter as a Google Doc (claude.ai) and one line:
   verdict, tells fixed, new names. Then snapshot it so their edits can be
   learned later (see Learning from their edits).

## Triage before revising

When the author wants to review or mark up a draft (theirs or a Claude draft
they haven't read), call `triage_scenes` with the chapter split at its own
scene breaks. Wait for the `[prose-forge triage]` reply (an "Updated" one
replaces the earlier). Then work it in order: `FIX STORY` scenes first (their
note says what should happen; quoted lines show where), then `CUT`, then
`FIX VOICE` with the drafting loop above, using `compare_passages` on their
quoted lines. Leave `KEEP` scenes untouched. Unmarked scenes: ask, don't guess.
If they prefer to annotate in Google Docs instead, read the doc with comments
included and treat comments starting `story:`, `voice:`, `cut:` or `keep:` the
same way.

## Learning their taste (voice ledger)

The detector says *that* a passage drifts; only the author can say what they
would actually write. When `check_draft` returns a hotspot, or the author asks
"which sounds like me?":

1. Pick the spots: the lines that drift, each kept short (one sentence to one
   paragraph, the line plus just enough around it to read). Versions that
   differ in several places at once are hard to choose between. Write 1–2
   rewrites per spot from the voice card.
2. `compare_passages`: one spot as `passages` (original plus rewrites), or up
   to 12 as `spots=[{"context": "scene 3, Matt's coat", "passages": [...]}]`,
   each with a context line saying where it is. Shuffle each spot's versions,
   no labels (a blind pick). Show one picker at a time and wait for its reply
   starting `[prose-forge pick]` (it repeats the context and gives one line
   per spot) before showing another; don't choose for them.
3. Apply each choice (or their edited text) to the draft.
4. Add an entry per spot to `voice-ledger.md` in the `prose-forge` folder,
   right away, before more drafting: date, chapter, why, the chosen text, the
   rejected texts. Mark the chosen text `author-edited` only if they edited
   it, and any version that was the author's own prose `author-written`.
   Always append; never delete or rewrite entries. A pick message saying
   "Changed my mind" gets new entries marked `replaces the entries above for
   <context>`; replaced entries count for nothing.

Use the ledger:
- **Before drafting:** after the voice card, read the last ~5 ledger entries
  (chosen text + why). Their stated reasons outrank the voice card.
- **Rebuilding the profile:** when 10 entries have accrued since the last
  `Profile rebuilt` line in the ledger (each `edits` entry with spots counts
  as 5), or when asked, call
  `build_style_profile` with `samples` = `samples.md` + every `author-edited`
  chosen text whose edits were substantial (not a word or two) + every
  `author-written` text from `edits` entries, and
  `controls` = `controls.md` + every rejected text not marked
  `author-written` (Claude's imitations the author turned down). Skip
  replaced entries. Join each
  side with blank lines between texts; picks are short and only count pooled. A picked but
  unedited Claude passage goes in neither. Save the new profile over
  `style-profile.json`, append `Profile rebuilt from N entries (date)` to the
  ledger, and tell the author in one line.
- If they pick "None of these" (for a spot or the whole picker), ask what's
  off, and note it in the ledger.

Revising the author's own prose: steps 3–5 only, and propose before/after
pairs instead of rewriting; it's their text.

## Learning from their edits

When the author edits a draft Claude handed back, each rewritten paragraph is
the best evidence there is: Claude's version next to theirs. Learn it without
being asked.

- **At handoff** (Every draft, step 6): copy the draft doc with Drive
  `copy_file` into a `snapshots` folder inside `prose-forge` (create it if
  missing), titled `snapshot: <doc title>`. It's a server-side copy; no prose
  passes through chat. Append to the ledger `### <date> · <chapter> · handed
  back`, with the doc's ID and the snapshot's ID.
- **Learn** at the start of every draft or revision session, before reading
  the ledger, and whenever the author says "learn from my edits": for each
  `handed back` line with no later `edits` entry, get the doc's metadata.
  Skip it if it's unchanged since the snapshot, or was modified in the last
  24 hours (they may still be editing). Otherwise download both as
  `text/plain` (a big download is saved to a file; use that path) and run
  `python <this skill's folder>/scripts/edits.py SNAPSHOT DOC OUT.json`.
- Append one entry: `### <date> · <chapter> · edits`, a `Why (inferred):`
  line naming the 2–3 patterns their edits show (Claude's reading, so it
  never outranks a reason the author stated), then `Chosen (author-edited):`
  the `author` text of every `edit` spot and `Chosen (author-written):` every
  `added` spot, and `Rejected:` the `claude` text of every `edit` and `cut`
  spot, joined with ` / ` as in pick entries. No spots: append the entry with
  `no substantive edits`. Then trash the snapshot: each draft is learned once.
- Tell the author in one line ("Learned from your edits to Ch39: 14
  rewrites, 3 cuts").
- **Backfill** (a draft from before snapshots, or "learn from my edits to
  <chapter>"): the Claude side is the version Claude handed back, as its own
  doc or a copy the author makes from Docs version history; the author side
  is their finished chapter. If either sits inside a longer manuscript, cut
  out just that chapter first (`chunk.py split`, or the text between its
  heading and the next). Then run `edits.py` and log the entry as above.
- No code execution: read both docs and compare them by hand only if they're
  short; otherwise say it waits for a session that can run the script.
