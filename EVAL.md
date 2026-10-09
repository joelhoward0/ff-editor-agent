# What actually makes Claude's drafts read like the author

Experiments on one author's private serial fiction (18 of their chapters,
~104k words; a co-writer's 19 chapters as a second human). No author prose is
in this repo; scripts ran against a private copy. Six Claude drafts were
written by fresh subagents from flat beat summaries of six real chapters, with
the author's style guides, never seeing the original.

## 1. Generic AI-tell lists don't find Claude-imitating-an-author

Seed banlist on the author's own prose: 0.23 hits per 1k words. Phrase mining
(control vs corpus n-grams) on 13k words of imitation found only plot nouns.
When Claude imitates a specific author it doesn't use stock phrases. It
**overcorrects**: shorter sentences (9.0 vs 10.9 words), more paragraphs ending
on a short line (58% vs 46%), far fewer dashes (1.6 vs 5.4 per 1k), avoidance
of words near anything the guide bans, and tics like "doesn't" (12× in one
construction).

Also found: single-newline paragraphs (Google Docs exports) were being read as
one giant paragraph. Fixed in `stats.py`.

## 2. A personal detector works; a generic one doesn't

| method (800-word windows, held-out chapters) | AUC Claude vs author |
|---|---|
| Burrows' Delta, top-150 words, equal weights | 0.58 |
| Delta weights learned from 3 Claude imitations of the author ("controls"), all 12 remaining chapters as samples | 0.98 (20 splits, min 0.92) |
| same, realistic 20k-word sample, function words only | 0.96 |
| same, top-6 features held out of the score (anti-gaming gate) | 0.91 |

The learned axis is specific to Claude: the co-writer (a different human)
scores 0.70–0.77 on it, not ~1.0.

Gate verdicts at 20k words of samples: the author's unseen chapters read
"like the author" 88% of the time and "imitation" 6%; unseen Claude drafts
are caught 95% of the time.

## 3. Fixing numbers is Goodhart; the real lever is the author's prose in context

Blind three-way judging (an LLM judge given two reference chapters; it picked
the real chapter 12/12 times):

- Revising a draft against the style guide: no change in score or judged voice.
- Revising against the drift report: score dropped a lot, but the features it
  wasn't shown didn't move, and the judge ranked it no better (3–3).
- **Drafting with a voice card (an empirical description of how the author
  really writes, written from 3 of their chapters) plus ~2k words of their
  recent prose:** judged closer to the author than the original draft 6/6.

The style guide described the author's aspirations (spare, literary).
Their real prose is looser, funnier, more conversational and more
sentimental, and the judge keyed on exactly that.

## Design consequences

- Setup: voice card + 3 control drafts → `build_style_profile(samples, controls)`.
- Drafting: voice card + the author's own recent prose in context first.
- Gate: `check_draft` verdict scores only features **not** shown in its drift
  advice, so editing toward the advice can't pass the gate by itself.
  Thresholds come from each profile's own 3-fold cross-validation.
- Limits: one author, one model family as both imitator and judge, six
  imitation chapters. Treat the numbers as directional.
