import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def load_adapter():
    """按文件路径加载本地 MCP adapter，避免要求 mcp 目录变成包。"""
    module_path = Path(__file__).resolve().parents[1] / "mcp" / "search_gateway_mcp.py"
    spec = importlib.util.spec_from_file_location("search_gateway_mcp", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_worker():
    """按文件路径加载远端 worker，单测只测纯函数分支，不触发真实 SSH。"""
    module_path = Path(__file__).resolve().parents[1] / "mcp" / "remote_gateway_worker.py"
    spec = importlib.util.spec_from_file_location("remote_gateway_worker", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def send_framed(proc, message):
    """按 MCP stdio framing 发送请求，模拟真实 CLI 客户端。"""
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    proc.stdin.write(b"Content-Length: " + str(len(raw)).encode("ascii") + b"\r\n\r\n" + raw)
    proc.stdin.flush()


def recv_framed(proc):
    """读取一条 MCP stdio framing 响应；EOF 会让测试失败而不是假阳性。"""
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        chunk = proc.stdout.read(1)
        if not chunk:
            stderr = proc.stderr.read().decode("utf-8", "ignore")
            raise AssertionError(f"MCP adapter closed transport: returncode={proc.poll()} stderr={stderr!r}")
        header += chunk

    length = int(header.split(b"Content-Length:", 1)[1].split(b"\r\n", 1)[0].strip())
    body = proc.stdout.read(length)
    return json.loads(body.decode("utf-8"))


def test_mcp_initialize_returns_server_info():
    adapter = load_adapter()
    response = adapter.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )

    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "ai-search-gateway"
    assert response["result"]["capabilities"]["tools"]["listChanged"] is False


def test_mcp_tool_argument_error_stays_json_rpc_error():
    adapter = load_adapter()
    response = adapter.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ai_search", "arguments": {}},
        }
    )

    assert response["id"] == 2
    assert response["error"]["code"] == -32000
    assert "缺少必要字符串参数" in response["error"]["message"]


def test_parse_remote_json_ignores_ssh_banner_lines():
    adapter = load_adapter()
    parsed = adapter.parse_remote_json('Welcome\n{"ok": true, "status": 200, "data": {"success": true}}\n')

    assert parsed == {"ok": True, "status": 200, "data": {"success": True}}


def test_mcp_ssh_target_uses_private_client_configuration(monkeypatch):
    adapter = load_adapter()
    monkeypatch.setenv("MCP_SEARCH_GATEWAY_SSH_HOST", "gateway-host")
    monkeypatch.setenv("MCP_SEARCH_GATEWAY_REMOTE_DIR", "/srv/search-gateway")

    command = adapter.build_ssh_command("remote_gateway_call.py")

    assert "gateway-host" in command
    assert command[-1] == "/srv/search-gateway/mcp/remote_gateway_call.py"


def test_mcp_ssh_target_requires_explicit_host(monkeypatch):
    adapter = load_adapter()
    monkeypatch.delenv("MCP_SEARCH_GATEWAY_SSH_HOST", raising=False)

    with pytest.raises(adapter.RemoteSessionError, match="MCP_SEARCH_GATEWAY_SSH_HOST"):
        adapter.build_ssh_command("remote_gateway_call.py")


def test_mcp_stdio_framed_tools_do_not_close_transport():
    """真实启动 adapter：初始化、列工具、参数错误都必须保持 JSON-RPC 响应。"""
    script = Path(__file__).resolve().parents[1] / "mcp" / "search_gateway_mcp.py"
    proc = subprocess.Popen(
        [sys.executable, "-B", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        send_framed(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
        )
        init_response = recv_framed(proc)
        assert init_response["result"]["serverInfo"]["name"] == "ai-search-gateway"

        send_framed(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send_framed(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_response = recv_framed(proc)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        assert {
            "ai_search",
            "ai_evidence_search",
            "ai_answer_snapshot",
            "ai_extract",
            "ai_fetch_page",
            "ai_screenshot",
            "ai_summary",
            "ai_analyze_url",
            "ai_research",
            "ai_ipinfo",
            "gateway_health",
        } <= tool_names
        search_tool = next(tool for tool in tools_response["result"]["tools"] if tool["name"] == "ai_search")
        evidence_tool = next(
            tool for tool in tools_response["result"]["tools"] if tool["name"] == "ai_evidence_search"
        )
        assert "普通搜索请保持 provider=auto" in search_tool["description"]
        assert "普通搜索优先 SearXNG" not in search_tool["description"]
        assert {
            "duckduckgo",
            "github",
            "stackexchange",
            "wikipedia",
            "wikidata",
            "hackernews",
            "arxiv",
            "openalex",
            "crossref",
            "pubmed",
            "semantic_scholar",
            "internet_archive",
            "common_crawl",
            "zhihu",
        } <= set(
            search_tool["inputSchema"]["properties"]["provider"]["enum"]
        )
        assert "zhihu" in evidence_tool["inputSchema"]["properties"]["providers"]["items"]["enum"]

        send_framed(
            proc,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ai_search", "arguments": {}}},
        )
        error_response = recv_framed(proc)
        assert error_response["id"] == 3
        assert error_response["error"]["code"] == -32000
        assert "缺少必要字符串参数" in error_response["error"]["message"]
        assert proc.poll() is None
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_mcp_stdio_accepts_lowercase_content_length_header():
    """不同 MCP 宿主可能发小写 header；adapter 必须保持 framed stdio，不可退回逐行 JSON。"""
    script = Path(__file__).resolve().parents[1] / "mcp" / "search_gateway_mcp.py"
    proc = subprocess.Popen(
        [sys.executable, "-B", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        proc.stdin.write(b"content-length: " + str(len(raw)).encode("ascii") + b"\r\n\r\n" + raw)
        proc.stdin.flush()

        response = recv_framed(proc)
        assert response["result"]["serverInfo"]["name"] == "ai-search-gateway"
        assert proc.poll() is None
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_mcp_new_analysis_tools_call_expected_routes(monkeypatch):
    adapter = load_adapter()
    calls = []

    def fake_call_gateway(payload, timeout=180):
        calls.append({"payload": payload, "timeout": timeout})
        return {"ok": True, "data": {"success": True}}

    monkeypatch.setattr(adapter, "call_gateway", fake_call_gateway)

    adapter.handle_tool_call(
        "ai_research",
        {
            "query": "Godot AI news",
            "provider": "grok",
            "max_results": 4,
            "max_sources": 2,
            "include_markdown": True,
        },
    )
    adapter.handle_tool_call(
        "ai_analyze_url",
        {"url": "https://example.com", "question": "What changed?"},
    )
    adapter.handle_tool_call("ai_fetch_page", {"url": "https://example.com"})

    assert calls[0]["payload"]["path"] == "/research"
    assert calls[0]["payload"]["body"] == {
        "query": "Godot AI news",
        "provider": "grok",
        "max_results": 4,
        "max_sources": 2,
        "include_markdown": True,
        "screenshot_mode": "auto",
    }
    assert calls[1]["payload"]["path"] == "/analyze-url"
    assert calls[1]["payload"]["body"] == {
        "url": "https://example.com",
        "question": "What changed?",
        "screenshot_mode": "auto",
    }
    assert calls[2]["payload"]["path"] == "/extract"
    assert calls[2]["payload"]["body"] == {"url": "https://example.com", "screenshot_mode": "auto"}


def test_mcp_evidence_tools_call_versioned_routes(monkeypatch):
    adapter = load_adapter()
    calls = []

    def fake_call_gateway(payload, timeout=180):
        calls.append({"payload": payload, "timeout": timeout})
        return {"ok": True, "data": {"success": True}}

    monkeypatch.setattr(adapter, "call_gateway", fake_call_gateway)

    adapter.handle_tool_call(
        "ai_evidence_search",
        {
            "queries": ["one", "two"],
            "locale": "zh-CN",
            "providers": ["brave", "tavily"],
            "max_results": 6,
            "include_domains": ["example.com"],
            "max_provider_calls": 2,
            "max_extract_pages": 3,
            "timeout_ms": 9000,
            "rerank": False,
        },
    )
    adapter.handle_tool_call("ai_answer_snapshot", {"queries": ["one"], "locale": "en-US"})

    assert calls[0]["payload"]["path"] == "/v1/evidence-search"
    assert calls[0]["payload"]["body"] == {
        "queries": ["one", "two"],
        "locale": "zh-CN",
        "providers": ["brave", "tavily"],
        "max_results": 6,
        "filters": {
            "include_domains": ["example.com"],
            "exclude_domains": [],
            "freshness": None,
        },
        "budget": {
            "max_provider_calls": 2,
            "max_extract_pages": 3,
            "timeout_ms": 9000,
        },
        "rerank": False,
    }
    assert calls[1]["payload"]["path"] == "/v1/answer-snapshots"
    assert calls[1]["payload"]["body"] == {"queries": ["one"], "locale": "en-US"}


def test_mcp_screenshot_tool_calls_screenshot_route(monkeypatch):
    adapter = load_adapter()
    calls = []

    def fake_call_gateway(payload, timeout=180):
        calls.append({"payload": payload, "timeout": timeout})
        return {"ok": True, "data": {"success": True}}

    monkeypatch.setattr(adapter, "call_gateway", fake_call_gateway)

    adapter.handle_tool_call(
        "ai_screenshot",
        {
            "url": "https://example.com",
            "provider": "apiflash",
            "width": 1024,
            "height": 768,
            "full_page": True,
            "format": "jpg",
            "wait_until": "network_idle",
            "delay_ms": 500,
        },
    )

    assert calls[0]["payload"]["path"] == "/screenshot"
    assert calls[0]["payload"]["body"] == {
        "url": "https://example.com",
        "provider": "apiflash",
        "width": 1024,
        "height": 768,
        "full_page": True,
        "format": "jpg",
        "wait_until": "network_idle",
        "delay_ms": 500,
    }


def test_remote_worker_config_errors_are_json_results():
    """远端 worker 配置异常时返回 JSON 错误，避免 SSH worker 裸退出。"""
    worker = load_worker()

    missing_key = worker.call_gateway({}, {"path": "/health"})
    assert missing_key["ok"] is False
    assert "GATEWAY_API_KEY" in missing_key["error"]

    missing_path = worker.call_gateway({"GATEWAY_API_KEY": "test"}, {})
    assert missing_path["ok"] is False
    assert "path" in missing_path["error"]
