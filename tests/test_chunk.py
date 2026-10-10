import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "plugin/skills/write-in-my-voice/scripts/chunk.py"
spec = importlib.util.spec_from_file_location("chunk", SCRIPT)
chunk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chunk)


def _para(style: str, text: str) -> dict:
    return {"paragraph": {"elements": [{"textRun": {"content": text + "\n"}}],
                          "paragraphStyle": {"namedStyleType": style}}}


def test_doc_json_splits_at_headings_and_scene_breaks(tmp_path):
    long_scene = " ".join(["word"] * 300)
    doc = {"content": {"tabs": [{"documentTab": {"body": {"content": [
        _para("HEADING_1", "Chapter 1"), _para("NORMAL_TEXT", "Matt lit a cigarette."),
        _para("HEADING_1", "Chapter 2"), _para("NORMAL_TEXT", long_scene),
        _para("NORMAL_TEXT", "—"), _para("NORMAL_TEXT", long_scene),
    ]}}}]}}
    src = tmp_path / "doc.json"
    src.write_text(json.dumps(doc))
    chunk.main(["split", str(src), str(tmp_path / "out"), "--max-words", "400"])
    index = json.loads((tmp_path / "out/index.json").read_text())
    assert [c["title"] for c in index] == ["Chapter 1", "Chapter 2 (part 1)", "Chapter 2 (part 2)"]
    assert all(c["words"] <= 400 for c in index)


def test_drive_markdown_and_names(tmp_path):
    src = tmp_path / "read.json"
    src.write_text(json.dumps({"fileContent": "# **Ch 1**\n\nThen Jory waved at Magda\\! "
                               "The goats yelled. Jory left."}))
    text = chunk.load(src)
    assert "\\" not in text and "**" not in text
    assert chunk.names_extract(text) == "Then Jory waved at Magda!"


def test_plain_text_pov_headings(tmp_path):
    src = tmp_path / "part.txt"
    src.write_text("Part 5\nJAMES[a][b]\nHe opens it.\nCOURTNEY\nTwo lines.\n"
                   "JAMIE\nThe lamp flame.\n")
    chunk.main(["split", str(src), str(tmp_path / "out")])
    index = json.loads((tmp_path / "out/index.json").read_text())
    assert [c["title"] for c in index][-3:] == ["JAMES", "COURTNEY", "JAMIE"]
