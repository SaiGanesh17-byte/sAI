"""A tiny MCP stdio server for tests. Deliberately chatty: it sends a log
notification and a server->client request before answering tools/list, and
pages its tool list, to exercise the client's message routing."""
import json
import sys


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


TOOLS = [
    {"name": "echo", "description": "Echo text back", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    {"name": "fail", "description": "Always errors", "inputSchema": {"type": "object", "properties": {}}},
]

print("fake server starting", file=sys.stderr, flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue  # notification
    if "result" in msg or "error" in msg:
        continue  # reply to our own server->client request
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0.1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "listing"}})
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
        if msg.get("params", {}).get("cursor") == "page2":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS[2:]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS[:2], "nextCursor": "page2"}})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments", {})
        if name == "echo":
            result = {"content": [{"type": "text", "text": args.get("text", "")}]}
        elif name == "add":
            result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        else:
            result = {"content": [{"type": "text", "text": "something broke"}], "isError": True}
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
