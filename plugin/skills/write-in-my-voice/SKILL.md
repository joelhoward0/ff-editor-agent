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

## Signed-in connection (prose-forge has `load_voice`)

If the prose-forge tools include `load_voice`, the author signed in with
Google: their voice card, style profile and voice ledger live in a
`prose-forge` folder in their own Drive, managed by the tools. Then:

- Start every draft or revision with `load_voice`; it returns the voice card
  and their recent picks, and retrains the profile when enough picks accrue.
- `check_draft` uses the saved profile; don't pass one.
- `build_style_profile` saves profile, samples and controls to their Drive.
- A `[prose-forge pick]` message ending "(Saved to your voice ledger.)" is
  already recorded. Without that line, call `record_pick` yourself.
- First time, if `load_voice` has no voice card or profile but the author has
  them elsewhere (e.g. "Voice Card" and style-profile.json in Drive), read them
  and import with `save_voice_file`. A profile built before signing in has no
  samples saved, so retraining needs one `build_style_profile` run.
- The file steps below (voice-card.md, style-profile.json, voice-ledger.md)
  are for the open connection only.

## One-time setup (no style-profile.json yet)

1. **Collect samples:** 15,000–30,000 words the author wrote themselves, never
   AI drafts. Look in Google Drive first (their manuscript docs; ask which
   chapters are fully theirs, and in co-written work which POV/chapters are
   theirs), else ask them to paste or attach.
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
5. Optional: phrases they personally hate, one per line, in `banlist.txt`.

Save everything beside the manuscript: a Drive folder (claude.ai) or the
project root (Claude Code, where `style-profile.json` also switches on the
automatic check hook).

## Every draft

1. **Load the real voice:** `voice-card.md`, plus the last ~2,000 words of the
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
   `new_names` for the author to confirm.
6. **Hand back** the chapter and one line: verdict, tells fixed, new names.

## Triage before revising

When the author wants to review or mark up a draft (theirs or a Claude draft
they haven't read), call `triage_scenes` with the chapter split at its own
scene breaks. Wait for the `[prose-forge triage]` reply (an "Updated" one
replaces the earlier). Then work it in order: `FIX STORY` scenes first (their
note says what should happen; quoted lines show where), then `CUT`, then
`FIX VOICE` with the drafting loop below, using `compare_passages` on their
quoted lines. Leave `KEEP` scenes untouched. Unmarked scenes: ask, don't guess.
If they prefer to annotate in Google Docs instead, read the doc with comments
included and treat comments starting `story:`, `voice:`, `cut:` or `keep:` the
same way.

## Learning their taste (voice ledger)

The detector says *that* a passage drifts; only the author can say what they
would actually write. When `check_draft` returns a hotspot, or the author asks
"which sounds like me?":

1. Take the passage (~150–400 words). Write 1–2 rewrites from the voice card.
2. `compare_passages([...])` with the original and rewrites in shuffled
   order, no labels (a blind pick). Wait for the reply starting
   `[prose-forge pick]`; don't choose for them.
3. Apply their choice (or their edited text) to the draft.
4. Append an entry to `voice-ledger.md` beside the profile (Drive doc or
   project file): date, chapter, why, the chosen text, the rejected texts.
   Mark the chosen text `author-edited` only if they edited it.

Use the ledger:
- **Before drafting:** after the voice card, read the last ~5 ledger entries
  (chosen text + why). Their stated reasons outrank the voice card.
- **Rebuilding the profile** (every ~10 entries, or when asked): rejected
  passages are Claude's imitations, so add them to `controls`.
  `author-edited` text counts as their prose, so add it to `samples`. A picked
  but unedited Claude passage is neither; it's still Claude's prose.
- If they pick "None of these", ask what's off, and note it in the ledger.

Revising the author's own prose: steps 3–5 only, and propose before/after
pairs instead of rewriting; it's their text.
