import json
import os
import pathlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Response
from fastapi.responses import HTMLResponse

import bot_config
from bot_config import ALL_TOOL_GROUPS

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
PORT = int(os.getenv("ADMIN_PORT", "8080"))

if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD env var is required")

app = FastAPI(docs_url=None, redoc_url=None)
COOKIE = "ops_session"
_sessions: dict[str, datetime] = {}


def _make_session() -> str:
    now = datetime.now()
    expired = [k for k, exp in _sessions.items() if now > exp]
    for k in expired:
        del _sessions[k]
    token = secrets.token_hex(32)
    _sessions[token] = now + timedelta(hours=8)
    return token


def _valid(token: Optional[str]) -> bool:
    if not token:
        return False
    exp = _sessions.get(token)
    if not exp:
        return False
    if datetime.now() > exp:
        del _sessions[token]
        return False
    return True


def require_auth(session: Optional[str] = Cookie(None, alias=COOKIE)):
    if not _valid(session):
        raise HTTPException(status_code=401, detail="Not authenticated")


@app.post("/api/login")
async def login(response: Response, username: str = Form(), password: str = Form()):
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _make_session()
    response.set_cookie(COOKIE, token, httponly=True, max_age=28800, samesite="lax")
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
async def me(_=Depends(require_auth)):
    return {"username": ADMIN_USERNAME}


@app.get("/api/config")
async def get_config(_=Depends(require_auth)):
    return bot_config.load()


@app.get("/api/tools")
async def get_tools(_=Depends(require_auth)):
    return {"groups": ALL_TOOL_GROUPS}


@app.post("/api/chats")
async def add_chat(body: dict, _=Depends(require_auth)):
    chat_id = int(body["chat_id"])
    thread_id = int(body["thread_id"]) if body.get("thread_id") not in (None, "", 0) else None
    name = body.get("name", str(chat_id))
    bot_config.add_chat(chat_id, thread_id, name)
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
async def remove_chat(chat_id: int, _=Depends(require_auth)):
    bot_config.remove_chat(chat_id)
    return {"ok": True}


@app.put("/api/chats/{chat_id}/tools")
async def update_tools(chat_id: int, body: dict, _=Depends(require_auth)):
    bot_config.update_tools(chat_id, body["tools"])
    return {"ok": True}


@app.put("/api/chats/{chat_id}/info")
async def update_info(chat_id: int, body: dict, _=Depends(require_auth)):
    thread_id = int(body["thread_id"]) if body.get("thread_id") not in (None, "", 0) else None
    bot_config.update_chat_info(chat_id, body.get("name", str(chat_id)), thread_id)
    return {"ok": True}


_LOG_RE = re.compile(r"^\[(.+?)\] (\w+) (.+)$")


def _parse_log_line(line: str) -> dict | None:
    m = _LOG_RE.match(line)
    if not m:
        return None
    ts, kind, rest = m.group(1), m.group(2), m.group(3)
    result: dict = {"ts": ts, "type": kind}
    user_m = re.search(r"\buser=(\d+)", rest)
    uname_m = re.search(r"\busername=(\S+)", rest)
    if user_m:
        result["user"] = user_m.group(1)
    if uname_m:
        result["username"] = uname_m.group(1)
    if kind == "MSG":
        text_m = re.search(r'text="(.*)"$', rest)
        result["detail"] = text_m.group(1) if text_m else rest
    elif kind == "TOOL":
        tool_m = re.search(r"\btool=(\S+)", rest)
        inputs_m = re.search(r"inputs=(\{.+\})$", rest)
        result["tool"] = tool_m.group(1) if tool_m else "?"
        if inputs_m:
            try:
                result["inputs"] = json.loads(inputs_m.group(1))
            except Exception:
                result["inputs"] = inputs_m.group(1)
        result["detail"] = result.get("tool", "")
    elif kind == "ERROR":
        tool_m = re.search(r"\btool=(\S+)", rest)
        error_m = re.search(r"error=(.+)$", rest)
        if tool_m:
            result["tool"] = tool_m.group(1)
        result["detail"] = error_m.group(1) if error_m else rest
    return result


@app.get("/api/audit")
async def get_audit(_=Depends(require_auth)):
    return bot_config.get_audit_config()


@app.put("/api/audit")
async def set_audit(body: dict, _=Depends(require_auth)):
    chat_id   = int(body["chat_id"])   if body.get("chat_id")   not in (None, "", 0) else None
    thread_id = int(body["thread_id"]) if body.get("thread_id") not in (None, "", 0) else None
    bot_config.set_audit_config(chat_id, thread_id)
    return {"ok": True}


@app.get("/api/logs")
async def get_logs(limit: int = 300, _=Depends(require_auth)):
    path = os.path.expanduser("~/.ops-bot-audit.log")
    if not os.path.exists(path):
        return {"entries": []}
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        entry = _parse_log_line(line)
        if entry:
            entries.append(entry)
        if len(entries) >= limit:
            break
    return {"entries": entries}


HTML = (pathlib.Path(__file__).parent / "templates" / "admin.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
