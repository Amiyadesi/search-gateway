#!/usr/bin/env python3
"""本地 MCP stdio 适配器：通过 SSH 调用远端 AI Search Gateway。"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any


SERVER_INFO = {"name": "ai-search-gateway", "version": "1.0.0"}
DEFAULT_MCP_TEXT_MAX_CHARS = 60000
DEFAULT_USE_PERSISTENT_SSH = True
DEFAULT_REMOTE_DIR = "/opt/search-gateway"
SEARCH_PROVIDERS = [
    "auto",
    "grok",
    "searxng",
    "brave",
    "tavily",
    "tavily_hikari",
    "exa",
    "context7",
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
]
SCREENSHOT_PROVIDERS = [
    "auto",
    "snapapi",
    "apiflash",
    "microlink",
    "screenshotlayer",
    "phantomjscloud",
    "screenshotbase",
    "screenshotscout",
    "screenshotmachine",
    "thumbnailws",
    "hqapi",
]
SCREENSHOT_MODES = ["auto", "never", "force"]


class RemoteSessionError(RuntimeError):
    """持久 SSH worker 不可用时抛出，外层会回落到单次 SSH 调用。"""


def get_ssh_host() -> str:
    host = os.environ.get("MCP_SEARCH_GATEWAY_SSH_HOST", "").strip()
    if not host:
        raise RemoteSessionError(
            "缺少 MCP_SEARCH_GATEWAY_SSH_HOST；请在本地 MCP 配置中指定 SSH host 或别名"
        )
    return host


def get_remote_dir() -> str:
    value = os.environ.get("MCP_SEARCH_GATEWAY_REMOTE_DIR", "").strip().rstrip("/")
    return value or DEFAULT_REMOTE_DIR


def build_ssh_command(helper_name: str, *, persistent: bool = False) -> list[str]:
    command = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        get_ssh_host(),
        "python3",
    ]
    if persistent:
        command.append("-u")
    command.append(f"{get_remote_dir()}/mcp/{helper_name}")
    return command


class RemoteGatewaySession:
    """复用一条 SSH 连接，避免每次 MCP 工具调用都重新握手。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.stdout_queue: queue.Queue[str | None] = queue.Queue()
        self.stderr_lines: list[str] = []
        self.lock = threading.Lock()

    def call(self, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        with self.lock:
            self._ensure_started()
            assert self.proc is not None
            assert self.proc.stdin is not None

            try:
                self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except Exception as exc:
                self.close()
                raise RemoteSessionError(f"写入远端 worker 失败: {exc!r}") from exc

            return self._read_json_response(timeout)

    def _ensure_started(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return

        self.close()
        self.stdout_queue = queue.Queue()
        self.stderr_lines = []
        try:
            self.proc = subprocess.Popen(
                build_ssh_command("remote_gateway_worker.py", persistent=True),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except Exception as exc:
            raise RemoteSessionError(f"无法启动远端 worker: {exc!r}") from exc

        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        threading.Thread(target=self._read_stdout, args=(self.proc.stdout,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc.stderr,), daemon=True).start()

    def _read_stdout(self, stream: Any) -> None:
        try:
            for line in stream:
                self.stdout_queue.put(line)
        finally:
            self.stdout_queue.put(None)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            text = line.strip()
            if text:
                self.stderr_lines.append(text)
                self.stderr_lines = self.stderr_lines[-20:]

    def _read_json_response(self, timeout: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                return {"ok": False, "status": 0, "error": f"远端搜索网关调用超时（{timeout}s）"}

            if self.proc is not None and self.proc.poll() is not None and self.stdout_queue.empty():
                stderr = "\n".join(self.stderr_lines[-5:])
                self.close()
                raise RemoteSessionError(stderr or "远端 worker 已退出")

            try:
                line = self.stdout_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue

            if line is None:
                stderr = "\n".join(self.stderr_lines[-5:])
                self.close()
                raise RemoteSessionError(stderr or "远端 worker stdout 已关闭")

            text = line.strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # SSH banner/MOTD 或远端杂音，忽略后继续等真正 JSON。
                continue

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


REMOTE_SESSION = RemoteGatewaySession()


def call_gateway(payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """不在本地保存网关密钥；每次经 SSH 在服务器内调用本机 API。"""
    use_persistent = os.environ.get("MCP_SEARCH_GATEWAY_PERSISTENT_SSH", "").strip().lower()
    persistent_enabled = DEFAULT_USE_PERSISTENT_SSH if not use_persistent else use_persistent not in {"0", "false", "no"}
    persistent_error = ""
    if persistent_enabled:
        try:
            return REMOTE_SESSION.call(payload, timeout=timeout)
        except RemoteSessionError as exc:
            # 远端 worker 尚未部署或连接被重置时，回落到旧的单次 SSH 调用。
            persistent_error = str(exc)

    result = call_gateway_once(payload, timeout=timeout)
    if persistent_error and not result.get("ok"):
        result["persistent_error"] = persistent_error
    return result


def call_gateway_once(payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """兼容路径：启动一次 SSH，调用一次远端 helper。"""
    try:
        proc = subprocess.run(
            build_ssh_command("remote_gateway_call.py"),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": 0, "error": f"SSH 调用搜索网关超时（{timeout}s）"}
    except FileNotFoundError as exc:
        return {"ok": False, "status": 0, "error": f"无法启动 SSH/Python: {exc}"}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": f"调用搜索网关失败: {exc!r}"}

    if proc.returncode != 0:
        return {"ok": False, "status": 0, "error": proc.stderr.strip() or proc.stdout.strip()}
    return parse_remote_json(proc.stdout)


def parse_remote_json(stdout: str) -> dict[str, Any]:
    """兼容 SSH banner/MOTD 污染：优先解析完整 stdout，失败后取最后一行 JSON。"""
    text = stdout.strip()
    if not text:
        return {"ok": False, "status": 0, "error": "远端搜索网关没有返回内容"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return {"ok": False, "status": 0, "error": "远端返回非 JSON 内容", "stdout_preview": text[:1000]}


def text_result(data: Any, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    max_chars = int(os.environ.get("MCP_SEARCH_GATEWAY_MAX_CHARS", DEFAULT_MCP_TEXT_MAX_CHARS))
    if max_chars > 0 and len(text) > max_chars:
        text = (
            text[:max_chars]
            + "\n\n...（MCP 输出已截断；如需完整内容，请缩小 max_results 或直接调用 /extract API。）"
        )
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
        "isError": is_error,
    }


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "ai_search",
            "description": "调用远端 AI Search Gateway 搜索。普通搜索请保持 provider=auto，让网关自动选择可靠上游并兜底；技术/AI/论文会自动偏向 Exa，实时/最新信息可显式选择 Grok；只有用户指定时才显式选择 SearXNG、文档、代码社区、百科、学术或开放数据 provider。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "provider": {
                        "type": "string",
                        "enum": SEARCH_PROVIDERS,
                        "default": "auto",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "ai_extract",
            "description": "用 Firecrawl 提取网页正文 markdown。兼容旧工具名；新客户端也可以使用 ai_fetch_page。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                    "screenshot_mode": {
                        "type": "string",
                        "enum": SCREENSHOT_MODES,
                        "default": "auto",
                        "description": "auto 会在正文抓取失败或过短时自动截图；force 主动截图；never 禁用截图。",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "ai_fetch_page",
            "description": "抓取单个网页并返回正文 markdown，适合后续让模型阅读或引用。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                    "screenshot_mode": {
                        "type": "string",
                        "enum": SCREENSHOT_MODES,
                        "default": "auto",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "ai_screenshot",
            "description": "主动截取网页截图，返回网关短期缓存图片 URL 和脱敏元数据。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                    "provider": {"type": "string", "enum": SCREENSHOT_PROVIDERS, "default": "auto"},
                    "width": {"type": "integer", "minimum": 320, "maximum": 3840, "default": 1280},
                    "height": {"type": "integer", "minimum": 240, "maximum": 2160, "default": 720},
                    "full_page": {"type": "boolean", "default": False},
                    "format": {"type": "string", "enum": ["png", "jpg", "jpeg", "webp"], "default": "png"},
                    "wait_until": {
                        "type": "string",
                        "enum": ["page_loaded", "network_idle", "dom_loaded"],
                        "default": "page_loaded",
                    },
                    "delay_ms": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
                },
                "required": ["url"],
            },
        },
        {
            "name": "ai_summary",
            "description": "自动搜索、提取网页、调用 AstrBot 同源高级模型总结。兼容旧工具名；更完整的研究链路可用 ai_research。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "需要总结的问题"},
                    "provider": {
                        "type": "string",
                        "enum": SEARCH_PROVIDERS,
                        "default": "auto",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "max_sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                    "screenshot_mode": {
                        "type": "string",
                        "enum": SCREENSHOT_MODES,
                        "default": "auto",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "ai_analyze_url",
            "description": "抓取单个网页并调用高级模型做智能分析；模型不可用时返回降级分析和原始 markdown。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                    "question": {
                        "type": "string",
                        "description": "希望模型围绕页面回答的问题；不传则做通用页面总结。",
                    },
                    "screenshot_mode": {
                        "type": "string",
                        "enum": SCREENSHOT_MODES,
                        "default": "auto",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "ai_research",
            "description": "完整研究工具：搜索、抓取多个来源、调用高级模型综合分析，并返回来源和可选正文上下文。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "研究问题"},
                    "provider": {
                        "type": "string",
                        "enum": SEARCH_PROVIDERS,
                        "default": "auto",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "max_sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                    "include_markdown": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否在 MCP 输出中包含抓取正文；默认只返回来源和摘要，减少输出体积。",
                    },
                    "screenshot_mode": {
                        "type": "string",
                        "enum": SCREENSHOT_MODES,
                        "default": "auto",
                        "description": "auto 在抓取失败/正文过短时自动截图；force 强制截图；never 禁用截图。",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "ai_ipinfo",
            "description": "查询 IP 属地、ASN、运营商、代理/VPN/云厂商等风险信息。",
            "inputSchema": {
                "type": "object",
                "properties": {"ip": {"type": "string", "description": "要查询的 IPv4 或 IPv6 地址"}},
                "required": ["ip"],
            },
        },
        {
            "name": "gateway_health",
            "description": "查看远端搜索网关健康状态。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "ai_search":
        query = require_text_arg(args, "query")
        provider = args.get("provider", "auto")
        max_results = clamp_int(args.get("max_results", 5), 1, 10)
        path = "/search?" + parse_query({"q": query, "provider": provider, "max_results": max_results})
        result = call_gateway({"method": "GET", "path": path, "timeout": 90}, timeout=120)
    elif name in {"ai_extract", "ai_fetch_page"}:
        result = call_gateway(
            {
                "method": "POST",
                "path": "/extract",
                "body": {
                    "url": require_text_arg(args, "url"),
                    "screenshot_mode": normalize_choice(args.get("screenshot_mode", "auto"), SCREENSHOT_MODES, "auto"),
                },
                "timeout": 120,
            },
            timeout=120,
        )
    elif name == "ai_screenshot":
        body = {
            "url": require_text_arg(args, "url"),
            "provider": normalize_choice(args.get("provider", "auto"), SCREENSHOT_PROVIDERS, "auto"),
            "width": clamp_int(args.get("width", 1280), 320, 3840),
            "height": clamp_int(args.get("height", 720), 240, 2160),
            "full_page": bool(args.get("full_page", False)),
            "format": normalize_choice(args.get("format", "png"), ["png", "jpg", "jpeg", "webp"], "png"),
            "wait_until": normalize_choice(
                args.get("wait_until", "page_loaded"),
                ["page_loaded", "network_idle", "dom_loaded"],
                "page_loaded",
            ),
            "delay_ms": clamp_int(args.get("delay_ms", 0), 0, 10000),
        }
        result = call_gateway({"method": "POST", "path": "/screenshot", "body": body, "timeout": 120}, timeout=160)
    elif name == "ai_summary":
        body = {
            "query": require_text_arg(args, "query"),
            "provider": normalize_provider(args.get("provider", "auto")),
            "max_results": clamp_int(args.get("max_results", 5), 1, 10),
            "max_sources": clamp_int(args.get("max_sources", 3), 1, 10),
            "screenshot_mode": normalize_choice(args.get("screenshot_mode", "auto"), SCREENSHOT_MODES, "auto"),
        }
        result = call_gateway(
            {"method": "POST", "path": "/summary", "body": body, "timeout": 180},
            timeout=220,
        )
    elif name == "ai_analyze_url":
        body = {"url": require_text_arg(args, "url")}
        question = optional_text_arg(args, "question")
        if question:
            body["question"] = question
        body["screenshot_mode"] = normalize_choice(args.get("screenshot_mode", "auto"), SCREENSHOT_MODES, "auto")
        result = call_gateway(
            {"method": "POST", "path": "/analyze-url", "body": body, "timeout": 180},
            timeout=220,
        )
    elif name == "ai_research":
        body = {
            "query": require_text_arg(args, "query"),
            "provider": normalize_provider(args.get("provider", "auto")),
            "max_results": clamp_int(args.get("max_results", 5), 1, 10),
            "max_sources": clamp_int(args.get("max_sources", 3), 1, 10),
            "include_markdown": bool(args.get("include_markdown", False)),
            "screenshot_mode": normalize_choice(args.get("screenshot_mode", "auto"), SCREENSHOT_MODES, "auto"),
        }
        result = call_gateway(
            {"method": "POST", "path": "/research", "body": body, "timeout": 180},
            timeout=220,
        )
    elif name == "ai_ipinfo":
        path = "/ipinfo?" + parse_query({"ip": require_text_arg(args, "ip")})
        result = call_gateway({"method": "GET", "path": path, "timeout": 30}, timeout=60)
    elif name == "gateway_health":
        result = call_gateway({"method": "GET", "path": "/health", "timeout": 30}, timeout=60)
    else:
        return text_result({"error": f"未知工具: {name}"}, is_error=True)

    return text_result(result.get("data") if result.get("ok") else result, is_error=not result.get("ok"))


def require_text_arg(args: dict[str, Any], name: str) -> str:
    """避免参数缺失时抛出 KeyError 导致客户端看到 transport closed。"""
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少必要字符串参数: {name}")
    return value.strip()


def optional_text_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if isinstance(value, str):
        return value.strip()
    return ""


def normalize_provider(value: Any) -> str:
    if isinstance(value, str) and value in SEARCH_PROVIDERS:
        return value
    return "auto"


def normalize_choice(value: Any, choices: list[str], default: str) -> str:
    if isinstance(value, str) and value in choices:
        return value
    return default


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    """把客户端传入的数字参数限制在 API 允许范围内。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def parse_query(params: dict[str, Any]) -> str:
    from urllib.parse import urlencode

    return urlencode(params)


class StdioTransport:
    def __init__(self) -> None:
        self.framed = False

    def read_message(self) -> dict[str, Any] | None:
        first = sys.stdin.buffer.readline()
        if not first:
            return None
        if self._looks_like_header(first):
            self.framed = True
            length = self._read_content_length(first)
            while True:
                line = sys.stdin.buffer.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                parsed_length = self._parse_content_length(line)
                if parsed_length is not None:
                    length = parsed_length
            if length is None:
                raise ValueError("MCP framed message missing Content-Length")
            raw = sys.stdin.buffer.read(length)
            return json.loads(raw.decode("utf-8-sig"))
        raw = first.strip()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8-sig"))

    def _looks_like_header(self, line: bytes) -> bool:
        stripped = line.strip()
        return b":" in stripped and not stripped.startswith((b"{", b"["))

    def _read_content_length(self, line: bytes) -> int | None:
        return self._parse_content_length(line)

    def _parse_content_length(self, line: bytes) -> int | None:
        name, sep, value = line.partition(b":")
        if not sep or name.strip().lower() != b"content-length":
            return None
        return int(value.strip())

    def write_message(self, message: dict[str, Any]) -> None:
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self.framed:
            sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        else:
            sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    if not message or "id" not in message:
        return None

    request_id = message["id"]
    method = message.get("method")
    params = message.get("params") or {}

    try:
        if method == "initialize":
            protocol_version = params.get("protocolVersion", "2024-11-05")
            result = {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            result = handle_tool_call(params.get("name"), params.get("arguments") or {})
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": repr(exc)},
        }


def main() -> None:
    transport = StdioTransport()
    while True:
        try:
            message = transport.read_message()
            if message is None:
                break
            response = handle(message)
            if response is not None:
                transport.write_message(response)
        except BrokenPipeError:
            break
        except Exception as exc:
            # 解析层异常也要回 JSON-RPC 错误，不能让 MCP stdio 进程直接退出。
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"MCP adapter parse error: {exc!r}"},
            }
            try:
                transport.write_message(error_response)
            except BrokenPipeError:
                break
    REMOTE_SESSION.close()


if __name__ == "__main__":
    main()
