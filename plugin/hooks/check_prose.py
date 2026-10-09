"""PostToolUse hook: lint any manuscript file Claude just wrote.

Stdlib only. Calls the hosted prose-forge server's check_draft tool and, when
the draft isn't clean, exits 2 so the findings go straight back to Claude to
fix. Only runs inside a project that has a style-profile.json at its root
(that file is the opt-in), and only for .md/.txt files.
"""

import json
import os
import sys
import urllib.request

URL = os.environ.get("PROSE_FORGE_URL", "https://prose-forge-tau.vercel.app/mcp")


def call(tool: str, args: dict) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(URL, body, {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(json.loads(resp.read())["result"]["content"][0]["text"])


def report(result: dict) -> str:
    lines = [f"prose-forge: {len(result['spans'])} AI tell(s), "
             f"{len(result['warnings'])} style warning(s). Rewrite ONLY these, "
             "keeping everything else verbatim:"]
    lines += [f'- "{s["text"]}" in: {s["sentence"]}' for s in result["spans"]]
    lines += [f"- {w['message']}" for w in result["warnings"]]
    return "\n".join(lines)


def main() -> int:
    event = json.load(sys.stdin)
    path = event.get("tool_input", {}).get("file_path", "")
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    profile_path = os.path.join(root, "style-profile.json")
    if not path.endswith((".md", ".txt")) or not os.path.exists(profile_path):
        return 0
    if os.path.basename(path) in ("README.md", "CLAUDE.md"):
        return 0
    banlist_path = os.path.join(root, "banlist.txt")
    try:
        result = call("check_draft", {
            "text": open(path, encoding="utf-8").read(),
            "profile": open(profile_path, encoding="utf-8").read(),
            "extra_banlist": open(banlist_path, encoding="utf-8").read()
            if os.path.exists(banlist_path) else "",
        })
    except Exception as exc:  # never block writing because the checker is down
        print(f"prose-forge check skipped: {exc}", file=sys.stderr)
        return 0
    if result.get("clean", True):
        return 0
    print(report(result), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
