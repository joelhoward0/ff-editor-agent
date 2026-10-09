# prose-forge

Makes Claude's fiction drafts sound like you, not like Claude.

## What you get

- **Write in my voice** (skill): learns your style once from your own
  chapters, then drafts and revises in it. After each draft it checks for AI
  tells, style drift and misspelled character names, and fixes what it finds.
- **Side-by-side picker**: when a passage doesn't sound like you, Claude shows
  you a few versions right in the chat. Pick one or edit one; your choices go
  into a voice ledger that makes the next draft closer.
- **Scene triage**: before revising, Claude shows the chapter scene by scene
  in the chat. Mark each one Keep / Fix story / Fix voice / Cut, add a note,
  quote the exact lines. Claude fixes story first, then cuts, then voice.
- **prose-forge connector**: the checker behind it. It stores nothing and
  sees only the text Claude sends it in that moment.

## First run

1. Connect Google Drive (or have your chapters ready to paste or attach).
2. Say: "Set up my writing style from my chapters in Drive."
3. Claude asks which chapters are fully yours, writes a voice card for you to
   correct, and saves a style profile next to your manuscript.

Then just ask: "Draft the next chapter," or "Revise chapter 12 in my voice."

## Privacy

Your profile, voice card and ledger live in your own Drive or project folder.
The hosted checker keeps no copy of anything you send it.

## Claude Code

    /plugin install prose-forge --marketplace joelhoward0/ff-editor-agent

The same plugin also adds an automatic check: in any project with a
`style-profile.json` at its root, every chapter file Claude writes gets
checked, and the findings go back to Claude to fix.
