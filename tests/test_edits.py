import base64
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "plugin/skills/write-in-my-voice/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("edits", SCRIPTS / "edits.py")
edits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edits)

DRAFT = """Jamie wakes in the dark.
It's cold water. It doesn't feel cold. Nothing does, anymore, not since the study.
Sexx is at the bench with "the coffee".
Ryan comes in, and nobody looks up.
"""
EDITED = """Jamie wakes in the dark.
He washes with cold water from the jug until it runs clear.
Sexx is at the bench with “the coffee.”
Ryan comes in, and nobody looks up.
Isla's asleep on the couch under Mo's blanket, and her boots are on the wrong feet.
"""


def test_diff_finds_rewrites_and_additions_and_ignores_trivia():
    out = edits.diff(DRAFT, EDITED)
    kinds = [s["kind"] for s in out["spots"]]
    assert kinds == ["edit", "added"]  # quote-style change on the Sexx line is not a spot
    edit, added = out["spots"]
    assert edit["claude"].startswith("It's cold water") and edit["author"].startswith("He washes")
    assert edit["at"] == "Jamie wakes in the dark."
    assert added["claude"] == "" and added["author"].startswith("Isla's asleep")
    assert out["summary"]["spots"] == 2 and out["summary"]["cut_words"] == 0


def test_cut_paragraph():
    out = edits.diff(DRAFT, DRAFT.replace("Ryan comes in, and nobody looks up.\n", ""))
    assert [(s["kind"], s["author"]) for s in out["spots"]] == [("cut", "")]


def test_cli_reads_drive_downloads(tmp_path):
    def dl(name, text):
        p = tmp_path / name
        p.write_text(json.dumps({"id": "x", "mimeType": "text/plain", "title": "t",
                                 "content": base64.b64encode(text.encode()).decode()}))
        return str(p)
    edits.main([dl("a.json", DRAFT), dl("b.json", EDITED), str(tmp_path / "out.json")])
    assert len(json.loads((tmp_path / "out.json").read_text())["spots"]) == 2
