#!/usr/bin/env python3
"""远端桥接脚本：读取 stdin JSON，带服务器本地密钥调用 Search Gateway。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error, request


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    configured_path = os.environ.get("MCP_GATEWAY_ENV_FILE", "").strip()
    env_path = Path(configured_path).expanduser() if configured_path else Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def main() -> None:
    env = read_env()
    payload = json.load(sys.stdin)
    method = payload.get("method", "GET")
    path = payload["path"]
    body = payload.get("body")
    timeout = payload.get("timeout", 120)

    headers = {"X-API-Key": env["GATEWAY_API_KEY"]}
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
            print(json.dumps({"ok": True, "status": resp.status, "data": result}, ensure_ascii=False))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw
        print(json.dumps({"ok": False, "status": exc.code, "error": detail}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "status": 0, "error": repr(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
