#!/usr/bin/env python3
"""远端常驻 worker：逐行读取 MCP adapter 请求并调用本机 Search Gateway。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error, request


def read_env() -> dict[str, str]:
    """读取服务器私有 .env，密钥不落到本地 CLI。"""
    env: dict[str, str] = {}
    configured_path = os.environ.get("MCP_GATEWAY_ENV_FILE", "").strip()
    env_path = Path(configured_path).expanduser() if configured_path else Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def call_gateway(env: dict[str, str], payload: dict) -> dict:
    """把一条 JSON 请求转成 localhost HTTP 调用。"""
    method = payload.get("method", "GET")
    path = payload.get("path")
    body = payload.get("body")
    timeout = payload.get("timeout", 120)
    api_key = env.get("GATEWAY_API_KEY")

    if not api_key:
        return {"ok": False, "status": 0, "error": "远端 .env 缺少 GATEWAY_API_KEY"}
    if not isinstance(path, str) or not path.startswith("/"):
        return {"ok": False, "status": 0, "error": "远端请求缺少合法 path"}

    headers = {"X-API-Key": api_key}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    gateway_url = env.get("MCP_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/")
    req = request.Request(
        gateway_url + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "status": resp.status, "data": result}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw
        return {"ok": False, "status": exc.code, "error": detail}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": repr(exc)}


def write_response(data: dict) -> None:
    """stdout 只写一行 JSON，避免污染 MCP stdio。"""
    print(json.dumps(data, ensure_ascii=False), flush=True)


def main() -> None:
    try:
        env = read_env()
        startup_error = ""
    except Exception as exc:
        env = {}
        startup_error = f"远端 worker 读取 .env 失败: {exc!r}"

    for line in sys.stdin:
        try:
            payload = json.loads(line)
            if startup_error:
                write_response({"ok": False, "status": 0, "error": startup_error})
                continue
            write_response(call_gateway(env, payload))
        except Exception as exc:
            write_response({"ok": False, "status": 0, "error": repr(exc)})


if __name__ == "__main__":
    main()
