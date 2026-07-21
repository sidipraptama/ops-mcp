import base64
import os
from mcp import StdioServerParameters
from dotenv import load_dotenv

load_dotenv()

_uvx = os.path.expanduser("~/.local/bin/uvx")
_PATH = os.environ.get("PATH", "")

# ── Telegram ──────────────────────────────────────────────────────────────────
AUDIT_CHAT_ID: int = -1004269056589
AUDIT_THREAD_ID: int | None = int(v) if (v := os.getenv("AUDIT_THREAD_ID")) else None

# ── Audit log ─────────────────────────────────────────────────────────────────
AUDIT_LOG_FILE: str = os.path.expanduser("~/.ops-bot-audit.log")

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

# ── SonarQube ─────────────────────────────────────────────────────────────────
# The Docker container downloads ~12 analyzer plugins at startup; the named
# volume caches them so restarts are fast and the full tool list registers.
_sonar_docker_env = ["-e", f"SONARQUBE_TOKEN={os.getenv('SONARQUBE_TOKEN', '')}"]
if os.getenv("SONARQUBE_URL"):
    _sonar_docker_env += ["-e", f"SONARQUBE_URL={os.getenv('SONARQUBE_URL')}"]
if os.getenv("SONARQUBE_ORG"):
    _sonar_docker_env += ["-e", f"SONARQUBE_ORG={os.getenv('SONARQUBE_ORG')}"]

# ── Jenkins ───────────────────────────────────────────────────────────────────
JENKINS_URL = os.getenv("JENKINS_URL", "")
JENKINS_USER = os.getenv("JENKINS_USER", "")
JENKINS_TOKEN = os.getenv("JENKINS_TOKEN", "")
_jenkins_auth = base64.b64encode(f"{JENKINS_USER}:{JENKINS_TOKEN}".encode()).decode()

# ── MCP ───────────────────────────────────────────────────────────────────────
# stdio servers — child processes the bot spawns
MCP_SERVERS: dict[str, StdioServerParameters] = {
    "grafana": StdioServerParameters(
        command=_uvx,
        args=["mcp-grafana"],
        env={
            "GRAFANA_URL": os.getenv("GRAFANA_URL", ""),
            "GRAFANA_SERVICE_ACCOUNT_TOKEN": os.getenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", ""),
            "PATH": _PATH,
        },
    ),
    # Read-only AWS API MCP — replaces the old hand-rolled boto3 tools.
    # READ_OPERATIONS_ONLY=true blocks any mutating API call.
    "aws": StdioServerParameters(
        command=_uvx,
        args=["awslabs.aws-api-mcp-server@latest"],
        env={
            "AWS_REGION": os.getenv("AWS_REGION", "ap-southeast-3"),
            "READ_OPERATIONS_ONLY": "true",
            "PATH": _PATH,
            # Credentials flow via the EC2 instance role (IMDS) — no keys here.
            **({"AWS_ACCESS_KEY_ID": v} if (v := os.getenv("AWS_ACCESS_KEY_ID")) else {}),
            **({"AWS_SECRET_ACCESS_KEY": v} if (v := os.getenv("AWS_SECRET_ACCESS_KEY")) else {}),
            **({"AWS_SESSION_TOKEN": v} if (v := os.getenv("AWS_SESSION_TOKEN")) else {}),
        },
    ),
    # SonarQube MCP — official Docker image, spawned over stdio.
    "sonarqube": StdioServerParameters(
        command="docker",
        args=["run", "-i", "--rm",
              "-v", "sonarqube-mcp-storage:/app/storage",
              *_sonar_docker_env, "mcp/sonarqube"],
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

# HTTP MCP servers — reached over Streamable HTTP, nothing spawned locally.
# Jenkins exposes MCP via the in-Jenkins `mcp-server` plugin. Only configured
# when JENKINS_URL is set, so the connection is skipped otherwise.
HTTP_MCP_SERVERS: dict[str, dict] = {}
if JENKINS_URL:
    HTTP_MCP_SERVERS["jenkins"] = {
        "url": JENKINS_URL.rstrip("/") + "/mcp-server/mcp",
        "headers": {"Authorization": f"Basic {_jenkins_auth}"},
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

# awslabs server exposes a small, generic tool set; all read-only under
# READ_OPERATIONS_ONLY=true, so allow them all.
AWS_TOOLS_WHITELIST: set[str] = {
    "call_aws",
    "suggest_aws_commands",
}

SONARQUBE_TOOLS_WHITELIST: set[str] = {
    "search_my_sonarqube_projects",
    "search_sonar_issues_in_projects",
    "get_component_measures",
    "get_quality_gate_status",
    "get_project_quality_gate_status",
    "list_quality_gates",
    "get_raw_source",
    "get_scm_info",
    "list_languages",
    "list_rule_repositories",
    "show_rule",
    "get_system_health",
    "get_system_info",
    "search_metrics",
}

# Read-only Jenkins tools only. triggerBuild/replayBuild/rebuildBuild are
# intentionally excluded — replayBuild runs modified Groovy on agents (RCE);
# defense-in-depth on top of the least-privilege Jenkins account.
JENKINS_TOOLS_WHITELIST: set[str] = {
    "getJobs",
    "getJob",
    "getBuild",
    "getBuildLog",
    "searchBuildLog",
    "getTestResults",
    "getBuildChangeSets",
    "findJobsWithScmUrl",
    "whoAmI",
    "getStatus",
}
