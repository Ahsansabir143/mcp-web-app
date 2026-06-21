"""
MCP deployment verification over SSE transport.

SSE-MCP protocol:
  GET /sse          -> long-lived stream; server sends endpoint URL, then
                       JSON-RPC responses as 'data:' lines
  POST /messages?.. -> 202 Accepted (no body); response arrives on SSE stream
"""
import asyncio
import json
import sys
import httpx

MCP_URL = "https://mcp-server-production-8d79.up.railway.app"
API_KEY = "16bVrH7gAVxhwtXUmdCcEnCUJEftVbwMND"
HDRS = {"X-API-Key": API_KEY}


class McpSession:
    def __init__(self):
        self.endpoint: str | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ready = asyncio.Event()

    def _dispatch(self, msg: dict):
        msg_id = msg.get("id")
        if msg_id and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if not fut.done():
                fut.set_result(msg)

    async def sse_reader(self, client: httpx.AsyncClient, done_event: asyncio.Event):
        sse_hdrs = {**HDRS, "Accept": "text/event-stream"}
        async with client.stream("GET", f"{MCP_URL}/sse", headers=sse_hdrs,
                                 timeout=httpx.Timeout(5.0, read=300.0)) as resp:
            async for line in resp.aiter_lines():
                if done_event.is_set():
                    break
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if "/messages?" in data:
                        self.endpoint = MCP_URL + data
                        self._ready.set()
                    else:
                        try:
                            msg = json.loads(data)
                            self._dispatch(msg)
                        except Exception:
                            pass

    async def rpc(self, client: httpx.AsyncClient, method: str, params: dict,
                  req_id: str, timeout: float = 30.0) -> dict:
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        r = await client.post(self.endpoint, json=payload,
                              headers={**HDRS, "Content-Type": "application/json"},
                              timeout=10)
        if r.status_code not in (200, 202):
            fut.cancel()
            return {"_http_error": r.status_code, "_body": r.text[:300]}
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"_timeout": f"no response in {timeout}s"}

    async def call_tool(self, client: httpx.AsyncClient, tool: str, args: dict) -> dict:
        resp = await self.rpc(client, "tools/call",
                              {"name": tool, "arguments": args}, req_id=tool)
        if "_http_error" in resp or "_timeout" in resp:
            return resp
        if "error" in resp:
            return {"_rpc_error": resp["error"]}
        content = resp.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except Exception:
                return {"_raw": content[0]["text"]}
        return resp

    async def list_tools(self, client: httpx.AsyncClient) -> list:
        resp = await self.rpc(client, "tools/list", {}, req_id="list_tools")
        return resp.get("result", {}).get("tools", [])


def sep(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


async def main():
    session = McpSession()
    done = asyncio.Event()

    async with httpx.AsyncClient() as client:
        reader_task = asyncio.create_task(session.sse_reader(client, done))

        try:
            await asyncio.wait_for(session._ready.wait(), timeout=12)
        except asyncio.TimeoutError:
            print("FAILED: SSE endpoint not received within 12s")
            done.set()
            sys.exit(1)

        print(f"SSE session: {session.endpoint}")

        try:
            # ── 1. Tool list ───────────────────────────────────────────────────
            sep("1. TOOL DISCOVERY")
            tools = await session.list_tools(client)
            tool_names = sorted(t["name"] for t in tools)
            print(f"  Total tools: {len(tools)}")
            print()
            for n in tool_names:
                marker = "  [NEW]" if n == "get_stream_health" else "      "
                print(f"  {marker} {n}")

            has_stream_health = "get_stream_health" in tool_names
            print()
            print(f"  get_stream_health present: {'YES' if has_stream_health else 'NO -- old build'}")

            # ── 2. Account connection status ───────────────────────────────────
            sep("2. get_account_connection_status")
            conn = await session.call_tool(client, "get_account_connection_status", {})
            print(json.dumps(conn, indent=2, default=str))

            # Check new fields
            accts = conn.get("accounts", [conn] if "account_id" in conn else [])
            print()
            if accts:
                a = accts[0]
                for field in ("source_of_truth", "stale", "stale_threshold_ms"):
                    present = field in a
                    val = a.get(field, "<MISSING>")
                    print(f"  {field}: {'PRESENT' if present else 'MISSING'} -> {val}")

            # ── 3. Stream health ───────────────────────────────────────────────
            sep("3. get_stream_health")
            if has_stream_health:
                health = await session.call_tool(client, "get_stream_health", {})
                print(json.dumps(health, indent=2, default=str))
            else:
                print("  SKIPPED -- tool not available on this build")
                health = {}

            # ── 4. Live trading safety ─────────────────────────────────────────
            sep("4. LIVE TRADING SAFETY CHECK")
            policy = await session.call_tool(client, "check_live_trade_policy",
                                             {"symbol": "BTCUSDT"})
            print(json.dumps(policy, indent=2, default=str))

            # ── 5. Verdict ─────────────────────────────────────────────────────
            sep("5. VERDICT")
            auth_ok = "_rpc_error" not in conn and "_http_error" not in conn
            new_build = has_stream_health and bool(accts and "stale" in accts[0])
            live_blocked = not policy.get("live_trading_enabled", True)

            stream_ok = False
            if health and "account_streams" in health:
                stream_ok = health.get("overall_healthy", False)

            print(f"  Auth working           : {'YES' if auth_ok else 'NO'}")
            print(f"  Latest build deployed  : {'YES' if new_build else 'NO -- missing get_stream_health or new fields'}")
            print(f"  Live trading blocked   : {'YES -- safe' if live_blocked else 'NO -- WARNING'}")
            print(f"  Stream health good     : {'YES' if stream_ok else 'UNKNOWN (old build) or unhealthy'}")

            if accts:
                a = accts[0]
                age_ms = a.get("stream_age_ms")
                if age_ms and age_ms > 120_000:
                    print(f"  stream_age_ms = {age_ms} ms ({age_ms//1000}s) -- STALE (>120s threshold)")
                elif age_ms:
                    print(f"  stream_age_ms = {age_ms} ms -- fresh")
                else:
                    print(f"  stream_age_ms = null -- no events received yet")

        finally:
            done.set()
            reader_task.cancel()
            try:
                await asyncio.wait_for(reader_task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass


if __name__ == "__main__":
    asyncio.run(main())
