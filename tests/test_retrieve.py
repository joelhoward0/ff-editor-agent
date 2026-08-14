"""TF-IDF retrieval: tag filtering with fallback, similarity ranking."""

from __future__ import annotations

from prose_forge import retrieve


def _chunk(cid: str, text: str, pov: str, scene_type: str) -> dict:
    return {"id": cid, "text": text, "tags": {"pov": pov, "scene_type": scene_type}}


CHUNKS = [
    _chunk("fog", "Fog rolled over the water and hid the channel markers from view. "
                  "The water lay flat and gray under the fog all morning.",
           "Maren", "interiority"),
    _chunk("market", "The market square filled with stalls and shouting vendors selling "
                     "bread and fish while children ran between the carts.",
           "Tobin", "action"),
    _chunk("argument", "Their argument moved from the kitchen to the porch and back, "
                       "words traded like cards neither wanted to hold.",
           "Maren", "dialogue"),
    _chunk("walk", "He walked the fence line counting posts until the counting became "
                   "its own kind of quiet.",
           "Tobin", "interiority"),
]


def test_filter_by_pov_and_scene_type():
    matched = retrieve.filter_chunks(CHUNKS, pov="Maren", scene_type="interiority")
    assert [c["id"] for c in matched] == ["fog"]


def test_fallback_to_scene_type_when_pov_unmatched():
    matched = retrieve.filter_chunks(CHUNKS, pov="Nobody", scene_type="interiority")
    assert {c["id"] for c in matched} == {"fog", "walk"}


def test_fallback_to_all_when_nothing_matches():
    matched = retrieve.filter_chunks(CHUNKS, pov="Nobody", scene_type="transition")
    assert len(matched) == len(CHUNKS)


def test_ranking_by_similarity():
    top = retrieve.top_matches(CHUNKS, "fog hiding the water and the channel", k=1)
    assert top[0]["id"] == "fog"


def test_k_limits_results():
    top = retrieve.top_matches(CHUNKS, "quiet counting", k=2)
    assert len(top) == 2


def test_empty_chunks_return_empty():
    assert retrieve.top_matches([], "anything", k=3) == []
