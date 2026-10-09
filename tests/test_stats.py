"""Every stats.py metric checked against hand-computed values."""

from __future__ import annotations

import math

import pytest

from prose_forge import stats


def test_word_count_keeps_contractions_and_hyphens():
    assert stats.word_count("It couldn't be a well-made thing.") == 6


def test_split_sentences_basic():
    text = "One two three. Four five? Six!"
    assert stats.split_sentences(text) == ["One two three.", "Four five?", "Six!"]


def test_sentence_spans_cover_text_exactly():
    text = 'He stopped. "No," she said.\n\nShe left'
    spans = stats.sentence_spans(text)
    assert "".join(text[s:e] for s, e in spans) == text


def test_paragraph_break_ends_a_sentence_without_punctuation():
    text = "no terminal punctuation here\n\nNext paragraph."
    assert stats.split_sentences(text) == [
        "no terminal punctuation here",
        "Next paragraph.",
    ]


def test_sent_len_mean_and_std():
    text = "One two three. Four five? Six!"
    # counts [3, 2, 1]: mean 2.0, population std sqrt(2/3)
    assert stats.sent_len_mean(text) == pytest.approx(2.0)
    assert stats.sent_len_std(text) == pytest.approx(math.sqrt(2 / 3))


def test_para_len_percentiles_linear_interpolation():
    text = "one two\n\none two three four\n\n" + " ".join(["w"] * 10)
    # counts [2, 4, 10]: p50 = 4.0, p90 = 4 + 0.8*(10-4) = 8.8
    p50, p90 = stats.para_len_percentiles(text)
    assert p50 == pytest.approx(4.0)
    assert p90 == pytest.approx(8.8)


def test_scene_markers_excluded_from_paragraphs():
    text = "First scene.\n\n***\n\nSecond scene."
    assert stats.split_paragraphs(text) == ["First scene.", "Second scene."]


def test_em_dash_per_1k():
    # 4 words, 2 em dashes -> 500 per 1k
    assert stats.em_dash_per_1k("He left — quickly — today.") == pytest.approx(500.0)


def test_semicolon_per_1k():
    # 4 words, 1 semicolon -> 250 per 1k
    assert stats.semicolon_per_1k("He left; she stayed.") == pytest.approx(250.0)


def test_dialogue_ratio_straight_quotes():
    text = '"Hi." He waved.'
    # 3 chars inside quotes / 15 total
    assert stats.dialogue_ratio(text) == pytest.approx(3 / 15)


def test_dialogue_ratio_curly_quotes():
    text = "“Hi.” He waved."
    assert stats.dialogue_ratio(text) == pytest.approx(3 / 15)


def test_adverb_tag_rate():
    text = "she said softly and he asked loudly"
    # 2 matches / 7 words -> 285.71 per 1k
    assert stats.adverb_tag_rate(text) == pytest.approx(2000 / 7)


def test_punchline_para_rate():
    long_para = (
        "This paragraph ends with a rather long final sentence that runs on "
        "for well over eight words in total."
    )
    short_para = "Some longer opening sentence sits here first. It ended."
    text = f"{long_para}\n\n{short_para}"
    assert stats.punchline_para_rate(text) == pytest.approx(0.5)


def test_compute_baseline_pools_across_texts():
    a = "One two three. Four five?"
    b = "Six!"
    baseline = stats.compute_baseline([a, b])
    # pooled counts [3, 2, 1]
    assert baseline["sent_len_mean"] == pytest.approx(2.0)
    assert baseline["sent_len_std"] == pytest.approx(math.sqrt(2 / 3))
    assert baseline["word_count"] == 6


def test_single_newline_paragraphs_and_sentences():
    # Google Docs exports: one newline between paragraphs, no blank lines
    text = "He ran.\nShe followed him down the hill\nand out."
    assert len(stats.split_paragraphs(text)) == 3
    assert stats.sentence_word_counts(text) == [2, 6, 2]
