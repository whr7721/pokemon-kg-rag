# -*- coding: utf-8 -*-
"""统一 Neo4j Query API(HTTP) 小客户端（绕开 bolt 限流），供 scripts/* 使用。"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def query(statement: str, params: dict | None = None, timeout: int = 60):
    host = os.getenv("NEO4J_URL", "").replace("neo4j+s://", "").replace("bolt://", "").split(":")[0]
    db = os.getenv("NEO4J_DB", "neo4j")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    if not host or "." not in host:
        raise RuntimeError("需要 NEO4J_URL/USER/PASSWORD/DB 环境变量（Query API 版）")
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    url = f"https://{host}/db/{db}/query/v2"
    body = json.dumps({"statement": statement, "parameters": params or {}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Basic " + cred, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errors"):
        raise RuntimeError("Neo4j: " + json.dumps(data["errors"])[:300])
    return (data.get("data") or {}).get("values", [])


def scalar(statement: str, params: dict | None = None):
    rows = query(statement, params)
    return rows[0][0] if rows else None