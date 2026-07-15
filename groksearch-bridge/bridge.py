from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="GrokSearch Bridge")


class BridgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)
    model: str | None = None
    platform: str = ""
    extra_sources: int = Field(default=3, ge=0, le=10)


class McpProcess:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> "McpProcess":
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.proc = await asyncio.create_subprocess_exec(
            "grok-search",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        await self.notify({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def notify(self, message: dict[str, Any]) -> None:
        await self._send(message)

    async def request(self, message: dict[str, Any]) -> dict[str, Any]:
        await self._send(message)
        expected_id = message.get("id")
        response = await asyncio.wait_for(self._read_response(expected_id), timeout=self.timeout)
        if response.get("error"):
            msg = response["error"].get("message") if isinstance(response["error"], dict) else str(response["error"])
            raise RuntimeError(msg)
        return response

    async def _read_response(self, expected_id: Any) -> dict[str, Any]:
        while True:
            response = await self._read_frame()
            if expected_id is None or response.get("id") == expected_id:
                return response

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("mcp process not running")
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        self.proc.stdin.write(frame)
        await self.proc.stdin.drain()

    async def _read_frame(self) -> dict[str, Any]:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("mcp stdout not available")
        header = await self._read_until(b"\r\n\r\n")
        length = 0
        for line in header.decode("ascii", "ignore").split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
                break
        if length <= 0:
            raise RuntimeError(f"missing MCP Content-Length: {header!r}")
        body = await self.proc.stdout.readexactly(length)
        return json.loads(body.decode("utf-8"))

    async def _read_until(self, marker: bytes) -> bytes:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("mcp stdout not available")
        data = bytearray()
        while not data.endswith(marker):
            chunk = await self.proc.stdout.read(1)
            if not chunk:
                stderr = await self._stderr_preview()
                raise RuntimeError(f"mcp process closed: {stderr}")
            data.extend(chunk)
        return bytes(data)

    async def _stderr_preview(self) -> str:
        if not self.proc or not self.proc.stderr:
            return ""
        try:
            data = await asyncio.wait_for(self.proc.stderr.read(2048), timeout=0.2)
            return data.decode("utf-8", "ignore")[-1000:]
        except Exception:
            return ""


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"success": True}


@app.post("/search")
async def search(payload: BridgeSearchRequest) -> dict[str, Any]:
    timeout = float(os.getenv("GROKSEARCH_BRIDGE_TIMEOUT_SECONDS", "180"))
    try:
        async with McpProcess(timeout=timeout) as mcp:
            search_result = await _call_tool(
                mcp,
                2,
                "web_search",
                {
                    "query": payload.query,
                    "platform": payload.platform,
                    "model": payload.model or "",
                    "extra_sources": payload.extra_sources,
                },
            )
            session_id = str(search_result.get("session_id") or "")
            sources_payload: dict[str, Any] = {}
            if session_id:
                sources_payload = await _call_tool(mcp, 3, "get_sources", {"session_id": session_id})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GrokSearch MCP failed: {type(exc).__name__}") from exc

    sources = sources_payload.get("sources") if isinstance(sources_payload, dict) else []
    results = _sources_to_results(sources, payload.max_results)
    if not results:
        results = _links_to_results(str(search_result.get("content") or ""), payload.max_results)
    return {
        "success": bool(results),
        "session_id": search_result.get("session_id"),
        "answer": search_result.get("content") or "",
        "sources_count": search_result.get("sources_count") or len(results),
        "results": results,
    }


async def _call_tool(mcp: McpProcess, request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = await mcp.request(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    result = response.get("result") or {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    content = result.get("content") or []
    if content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                return {"content": text, "sources_count": 0}
    return {}


def _sources_to_results(sources: Any, max_results: int) -> list[dict[str, str]]:
    if not isinstance(sources, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("href") or item.get("link")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = item.get("title") or item.get("name") or item.get("label") or url
        snippet = item.get("description") or item.get("snippet") or item.get("content") or ""
        out.append({"title": str(title)[:160], "url": url, "snippet": str(snippet)[:800]})
        if len(out) >= max_results:
            break
    return out


def _links_to_results(text: str, max_results: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    snippet = re.sub(r"\s+", " ", text).strip()[:800]
    for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
        cleaned = _clean_url(url)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append({"title": label.strip()[:160] or cleaned, "url": cleaned, "snippet": snippet})
        if len(out) >= max_results:
            return out
    for url in re.findall(r"https?://[^\s\])}>,]+", text):
        cleaned = _clean_url(url)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append({"title": cleaned, "url": cleaned, "snippet": snippet})
        if len(out) >= max_results:
            return out
    return out


def _clean_url(url: str) -> str:
    return url.strip().rstrip(".,;:)")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8010")))
