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
- **Learns from your edits**: edit a chapter Claude drafted, in the Google
  Doc as you normally would. Next session Claude compares its draft with your
  version, logs your rewrites and cuts, and drafts closer to you after that.
  It can also learn from chapters you've already edited ("learn from my
  edits to chapter 12").
- **prose-forge connector**: the checker behind it. It stores nothing and
  sees only the text Claude sends it in that moment.

## Install (claude.ai, web or desktop; any paid plan)

1. In the left sidebar open **Customize**, then the **Plugins** tab.
2. **Add** → **Add marketplace** → **Add from a repository**, and enter
   `joelhoward0/ff-editor-agent`.
3. Find **prose-forge** under **Discover** and add it.
4. Connect **Google Drive** and **Google Docs** under Connectors, so Claude
   can read your chapters and keep your voice files.

## First run

1. Say: "Set up my writing style from my chapters in Drive."
2. Claude asks which chapters are fully yours (in a co-written book, which
   POV or chapters you wrote), writes a voice card for you to correct, and
   builds a style profile. About 15,000 words of your own prose is enough.
3. Everything lands in a `prose-forge` folder in your Drive.

Then just ask: "Draft the next chapter," or "Revise chapter 12 in my voice."

## Privacy

Your profile, voice card, ledger and edit history live in a `prose-forge`
folder in your own Drive (or your project folder in Claude Code). When Claude
hands you a draft it keeps a short-lived copy there to compare against your
edits, and trashes it once it has learned from them. The hosted checker keeps
no copy of anything you send it.

## Claude Code

    /plugin install prose-forge --marketplace joelhoward0/ff-editor-agent

The same plugin also adds an automatic check: in any project with a
`style-profile.json` at its root, every chapter file Claude writes gets
checked, and the findings go back to Claude to fix.
