import json
import os
from threading import RLock

CONFIG_FILE = os.path.expanduser("~/.ops-bot-config.json")
_lock = RLock()

ALL_TOOL_GROUPS = [
    "aws", "bitbucket", "grafana", "sonarqube", "jenkins",
    "git-dora", "git-maps", "git-boots", "git-infra",
]

_DEFAULT_TOOLS = {g: True for g in ALL_TOOL_GROUPS}


def _default() -> dict:
    return {"chats": {}}


def load() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return _default()


def save(config: dict) -> None:
    with _lock:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)


def _thread_ids(chat: dict) -> list[int]:
    """Normalize old thread_id (int|None) to thread_ids (list[int])."""
    if "thread_ids" in chat:
        return chat["thread_ids"]
    old = chat.get("thread_id")
    return [old] if old is not None else []


def get_allowed_chats() -> dict[int, list[int]]:
    """Returns {chat_id: [thread_ids]} where empty list means all threads."""
    return {
        int(cid): _thread_ids(chat)
        for cid, chat in load().get("chats", {}).items()
    }


def get_tools_for_chat(chat_id: int) -> set[str]:
    """Returns set of enabled tool group names for this chat."""
    chat = load().get("chats", {}).get(str(chat_id), {})
    tools = chat.get("tools", _DEFAULT_TOOLS)
    return {g for g, enabled in tools.items() if enabled}


def add_chat(chat_id: int, thread_id: int | None, name: str) -> None:
    with _lock:
        config = load()
        chats = config.setdefault("chats", {})
        key = str(chat_id)
        if key in chats:
            existing = chats[key]
            tids = _thread_ids(existing)
            if thread_id is not None and thread_id not in tids:
                tids.append(thread_id)
            existing["thread_ids"] = tids
            existing.pop("thread_id", None)
        else:
            chats[key] = {
                "name": name,
                "thread_ids": [thread_id] if thread_id is not None else [],
                "tools": dict(_DEFAULT_TOOLS),
            }
        save(config)


def remove_thread(chat_id: int, thread_id: int) -> None:
    with _lock:
        config = load()
        chat = config.get("chats", {}).get(str(chat_id))
        if chat:
            chat["thread_ids"] = [t for t in _thread_ids(chat) if t != thread_id]
            chat.pop("thread_id", None)
            save(config)


def remove_chat(chat_id: int) -> None:
    with _lock:
        config = load()
        config.get("chats", {}).pop(str(chat_id), None)
        save(config)


def update_tools(chat_id: int, tools: dict[str, bool]) -> None:
    with _lock:
        config = load()
        chat = config.get("chats", {}).get(str(chat_id))
        if chat:
            chat["tools"] = tools
            save(config)


def update_chat_info(chat_id: int, name: str) -> None:
    with _lock:
        config = load()
        chat = config.get("chats", {}).get(str(chat_id))
        if chat:
            chat["name"] = name
            save(config)


def get_audit_config() -> dict:
    """Returns {'chat_id': int|None, 'thread_id': int|None}, falling back to config.py values."""
    from config import AUDIT_CHAT_ID, AUDIT_THREAD_ID
    cfg = load()
    audit = cfg.get("audit", {})
    return {
        "chat_id":   audit.get("chat_id",   AUDIT_CHAT_ID),
        "thread_id": audit.get("thread_id", AUDIT_THREAD_ID),
    }


def set_audit_config(chat_id: int | None, thread_id: int | None) -> None:
    with _lock:
        config = load()
        config["audit"] = {"chat_id": chat_id, "thread_id": thread_id}
        save(config)


def seed_defaults() -> None:
    if os.path.exists(CONFIG_FILE):
        return
    from config import AUDIT_CHAT_ID
    add_chat(AUDIT_CHAT_ID, 4, "Procal Ops")
    print(f"Seeded default chat config -> {CONFIG_FILE}")
