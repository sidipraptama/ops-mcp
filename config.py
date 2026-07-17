import os
from mcp import StdioServerParameters
from dotenv import load_dotenv

load_dotenv()

_uvx = os.path.expanduser("~/.local/bin/uvx")
_PATH = os.environ.get("PATH", "")

# ── Telegram ──────────────────────────────────────────────────────────────────
ALLOWED_CHAT_IDS: set[int] = {-1004269056589}
ALLOWED_THREAD_ID: int = 4  # topic/thread inside the group

# ── Claude ────────────────────────────────────────────────────────────────────
MAX_HISTORY = 5

# ── PR Polling ────────────────────────────────────────────────────────────────
POLL_REPO = "procal-infra-3"
POLL_TARGET_BRANCH = "main"
POLL_INTERVAL = 300  # seconds

# ── Bitbucket ─────────────────────────────────────────────────────────────────
BITBUCKET_WORKSPACE = os.getenv("BITBUCKET_WORKSPACE", "academytools")
BITBUCKET_USER = os.getenv("BITBUCKET_USER")
BITBUCKET_APP_PASSWORD = os.getenv("BITBUCKET_APP_PASSWORD")

# ── MCP ───────────────────────────────────────────────────────────────────────
MCP_SERVERS: dict[str, StdioServerParameters] = {
    "grafana": StdioServerParameters(
        command=_uvx,
        args=["mcp-grafana"],
        env={
            "GRAFANA_URL": os.getenv("GRAFANA_URL"),
            "GRAFANA_SERVICE_ACCOUNT_TOKEN": os.getenv("GRAFANA_SERVICE_ACCOUNT_TOKEN"),
            "PATH": _PATH,
        },
    ),
    "git-dora": StdioServerParameters(
        command=_uvx,
        args=["mcp-server-git", "--repository", os.path.expanduser("~/dora")],
        env={"PATH": _PATH},
    ),
    "git-maps": StdioServerParameters(
        command=_uvx,
        args=["mcp-server-git", "--repository", os.path.expanduser("~/maps")],
        env={"PATH": _PATH},
    ),
    "git-boots": StdioServerParameters(
        command=_uvx,
        args=["mcp-server-git", "--repository", os.path.expanduser("~/boots")],
        env={"PATH": _PATH},
    ),
    "git-infra": StdioServerParameters(
        command=_uvx,
        args=["mcp-server-git", "--repository", os.path.expanduser("~/procal-infra")],
        env={"PATH": _PATH},
    ),
}

GRAFANA_TOOLS_WHITELIST: set[str] = {
    "query_loki_logs",
    "find_error_pattern_logs",
    "query_prometheus",
    "query_prometheus_histogram",
    "list_incidents",
    "find_slow_requests",
    "list_datasources",
}

GIT_TOOLS_WHITELIST: set[str] = {
    "git_log",
    "git_diff",
    "git_show",
    "git_read_file",
    "git_status",
}
