---
name: write-in-my-voice
description: Draft or revise fiction (chapters, scenes, story prose) in the author's own voice, checked for AI tells, style drift and name continuity with earlier chapters. Use whenever the user asks to write, draft, continue, revise or polish a chapter, scene or story passage, or to set up their writing style, style profile or series bible.
---

# Write in my voice

You draft; the prose-forge tools measure. Never skip the checks: they catch
what reads fine to you but reads as AI to everyone else.

## One-time setup (no style-profile yet)

1. Collect 3,000–10,000 words the author wrote themselves, never AI drafts.
   Look in Google Drive first (search for their manuscript or chapter docs and
   ask which are fully their own writing), else ask them to paste or attach.
2. Call `build_style_profile` with the samples joined by blank lines.
3. Save the returned `profile` JSON as `style-profile.json`: in Drive next to
   the manuscript (claude.ai) or at the project root (Claude Code, where it
   also switches on the automatic check hook). Show the author the
   `drafting_guidance` in plain words.
4. Ask for any phrases they personally hate; save them one per line as
   `banlist.txt` beside the profile.

## Every draft

1. **Load context.** Read `style-profile.json`, `banlist.txt` if present, and
   the canon: the series bible plus the previous chapter or two (from Drive or
   the project). Note the last ~500 words of the previous chapter for voice.
2. **Draft** following `drafting_guidance` and the brief. Write freely: do
   not hold a list of banned phrases in mind while drafting, that makes them
   more likely. The check pass handles them.
3. **Check.** Call `check_draft` with the draft, the profile JSON string and
   the banlist text.
4. **Fix surgically.** Rewrite only the sentences named in `spans`, and adjust
   only what each warning names (sentence-length spread, em dashes,
   paragraph endings). Every unflagged sentence stays byte-for-byte. Re-check;
   stop after two fix rounds and report anything left.
5. **Continuity.** Call `check_continuity` with the draft and the canon text.
   Fix every `near_misses` spelling. List `new_names` for the author to confirm.
6. **Hand back** the chapter, then one short line: tells fixed, warnings left,
   new names to confirm. Save it beside the earlier chapters if that is
   where they live.

Revising the author's own prose: run steps 3–5 only, and propose changes as
before/after pairs instead of rewriting, since it is their text.
