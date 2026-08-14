"""Chunker boundaries, ingest idempotency, tagger fallback, baseline asset."""

from __future__ import annotations

import json

from prose_forge import corpus, llm


def _para(words: int, token: str = "word") -> str:
    return " ".join([token] * words)


class TestSceneBlocks:
    def test_marker_lines_split_scenes(self):
        text = "scene one text\n\n***\n\nscene two text\n\n---\n\nscene three text"
        assert corpus.split_scene_blocks(text) == [
            "scene one text",
            "scene two text",
            "scene three text",
        ]

    def test_two_blank_lines_split_scenes(self):
        assert corpus.split_scene_blocks("one\n\n\n\ntwo") == ["one", "two"]

    def test_single_blank_line_is_a_paragraph_not_a_scene(self):
        assert corpus.split_scene_blocks("one\n\ntwo") == ["one\n\ntwo"]


class TestPacking:
    def test_short_scene_stays_one_chunk(self):
        scene = _para(300)
        assert corpus.pack_paragraphs(scene) == [scene]

    def test_long_scene_packs_at_paragraph_boundaries(self):
        paras = [_para(200) for _ in range(10)]
        chunks = corpus.pack_paragraphs("\n\n".join(paras))
        from prose_forge.stats import word_count

        assert [word_count(c) for c in chunks] == [1400, 600]

    def test_oversize_single_paragraph_not_split(self):
        scene = _para(2000)
        assert corpus.pack_paragraphs(scene) == [scene]

    def test_chunk_text_respects_scene_breaks(self):
        # two short scenes must not merge into one chunk despite being < 600 words
        text = f"{_para(100, 'alpha')}\n\n***\n\n{_para(100, 'beta')}"
        chunks = corpus.chunk_text(text)
        assert len(chunks) == 2


def _write_corpus(root, name: str, text: str) -> None:
    d = root / "corpus"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


class TestIngest:
    def test_ingest_tags_and_is_idempotent(self, workspace, cfg):
        _write_corpus(workspace, "one.md", "She crossed the flats before dawn. " * 30)
        first = corpus.ingest(cfg)
        assert first["chunks"] >= 1
        assert first["tagged"] == first["chunks"]

        second = corpus.ingest(cfg)
        assert second["tagged"] == 0
        assert second["reused"] == first["chunks"]

        # editing the file re-chunks and re-tags it
        _write_corpus(workspace, "one.md", "Tobin waited by the pump house. " * 30)
        third = corpus.ingest(cfg)
        assert third["tagged"] >= 1

    def test_tags_come_from_tagger_json(self, workspace, cfg):
        _write_corpus(workspace, "one.md", "She crossed the flats before dawn. " * 30)
        corpus.ingest(cfg)
        record = corpus.load_chunks()[0]
        assert record["tags"] == {"pov": "Maren", "scene_type": "interiority", "tense": "past"}

    def test_unparseable_tagger_response_falls_back(self, workspace, cfg):
        (workspace / "tests/fixtures/mock_responses/tagger.md").write_text(
            "not json at all", encoding="utf-8"
        )
        llm.reset_mock_counters()
        _write_corpus(workspace, "one.md", "She crossed the flats before dawn. " * 30)
        corpus.ingest(cfg)
        assert corpus.load_chunks()[0]["tags"] == corpus.FALLBACK_TAGS


class TestBaselineAsset:
    def test_baseline_written_with_banlist_self_hits(self, workspace, cfg):
        _write_corpus(
            workspace,
            "one.md",
            "She crossed the flats before dawn. The pump house stood dark. " * 20,
        )
        corpus.ingest(cfg)
        result = corpus.build_baseline_asset()
        on_disk = json.loads((workspace / "data/baseline.json").read_text())
        assert on_disk == result
        for key in [
            "sent_len_mean", "sent_len_std", "para_len_words_p50", "para_len_words_p90",
            "em_dash_per_1k", "semicolon_per_1k", "dialogue_ratio", "adverb_tag_rate",
            "punchline_para_rate", "banlist_hits_per_1k", "word_count", "chunk_count",
        ]:
            assert key in on_disk
        assert on_disk["banlist_hits_per_1k"] == 0.0
