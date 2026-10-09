"""Remote MCP server, exercised over real streamable HTTP via the MCP client."""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import anyio
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import remote_server  # noqa: E402

CORPUS = "\n\n".join(p.read_text() for p in (ROOT / "tests/fixtures/corpus").glob("*.md"))
SLOP = (
    "Marisole couldn't help but smile. A mix of fear and hope bloomed in her chest, "
    "a testament to how far she had come. The silence stretched between them."
)
SLOP_LONG = "\n".join([SLOP] * 60)


def _serve() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(remote_server.app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/mcp"


def test_remote_tools_end_to_end():
    url = _serve()
    import httpx

    assert httpx.get(url, timeout=5).status_code == 405  # never an idle stream

    async def run():
        async with streamable_http_client(url) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {
                "build_style_profile", "check_draft", "check_continuity", "compare_passages"
            }
            tools = {t.name: t for t in (await s.list_tools()).tools}
            uri = tools["compare_passages"].meta["ui"]["resourceUri"]
            ui = (await s.read_resource(uri)).contents[0]
            assert ui.mime_type == "text/html;profile=mcp-app" and "ui/message" in ui.text

            async def call(tool, **args):
                res = await s.call_tool(tool, args)
                return json.loads(res.content[0].text)

            prof = await call("build_style_profile", samples=CORPUS * 3, controls=SLOP_LONG)
            assert prof["drafting_guidance"] and prof["imitation_tells"]
            lint = await call("check_draft", text=SLOP, profile=json.dumps(prof["profile"]))
            assert not lint["clean"]
            hit_text = {h["text"].lower() for h in lint["spans"]}
            assert {"couldn't help but", "a testament to", "the silence stretched"} <= hit_text
            assert all(h["sentence"] for h in lint["spans"])
            long = await call("check_draft", text=SLOP_LONG, profile=json.dumps(prof["profile"]))
            assert long["voice"]["verdict"] == "reads like an imitation"
            cont = await call("check_continuity", draft=SLOP, canon="She saw Marisol.")
            assert cont["near_misses"] == [{"draft": "Marisole", "canon": "Marisol"}]
            pick = await s.call_tool("compare_passages", {"passages": ["one", "two"]})
            assert pick.structured_content["passages"] == ["one", "two"]

    anyio.run(run)
