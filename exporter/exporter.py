import http.client
import json
import logging
import os
import re
import select
import socket
import socketserver
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


AGENT_PORT = int(os.environ.get("AGENT_PORT", "6556"))
DOCKER_API_HOST = os.environ.get("DOCKER_API_HOST", "docker-socket-proxy")
DOCKER_API_PORT = int(os.environ.get("DOCKER_API_PORT", "2375"))
CONFIG_PATH = Path(os.environ.get("EXPORTER_CONFIG", "/app/config.json"))
HOST_SYNC_DIR = Path(os.environ.get("HOST_SYNC_DIR", "/host-sync"))
HOST_SYNC_TEMPLATE = Path(os.environ.get("HOST_SYNC_TEMPLATE", "/app/host_sync.py"))
INVENTORY_MAX_AGE_SECONDS = int(os.environ.get("INVENTORY_MAX_AGE_SECONDS", "900"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)


class DockerApiError(RuntimeError):
    pass


class DockerApi:
    def __init__(self, host=DOCKER_API_HOST, port=DOCKER_API_PORT, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout

    def get_json(self, path):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read()
            if response.status < 200 or response.status >= 300:
                detail = body.decode("utf-8", errors="replace")[:500]
                raise DockerApiError(f"Docker API returned HTTP {response.status}: {detail}")
            return json.loads(body)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise DockerApiError(str(exc)) from exc
        finally:
            connection.close()

    def list_containers(self):
        return self.get_json("/containers/json?all=1")

    def inspect_container(self, container_id):
        return self.get_json(f"/containers/{quote(container_id, safe='')}/json")

    def container_stats(self, container_id):
        return self.get_json(
            f"/containers/{quote(container_id, safe='')}/stats?stream=false"
        )


def load_config(path=CONFIG_PATH):
    with path.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)
    blacklist = config.get("blacklist", [])
    if not isinstance(blacklist, list) or any(not isinstance(item, str) for item in blacklist):
        raise ValueError("config.json: 'blacklist' must be a list of project names")
    return {"blacklist": {item.strip() for item in blacklist if item.strip()}}


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def load_known_stacks(path=None):
    path = path or HOST_SYNC_DIR / "known_stacks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    stacks = data.get("stacks") if isinstance(data, dict) else None
    if not isinstance(stacks, list) or any(not isinstance(name, str) or not name for name in stacks):
        raise ValueError("known_stacks.json must contain a list of stack names")
    return {name.strip() for name in stacks if name.strip()}


def merge_known_stacks(observed_stacks, known_stacks, blacklist):
    stack_names = (set(observed_stacks) | set(known_stacks)) - blacklist
    return {
        name: observed_stacks.get(name, [])
        for name in sorted(stack_names, key=str.casefold)
    }


def save_known_stacks(stacks, path=None):
    path = path or HOST_SYNC_DIR / "known_stacks.json"
    data = {"stacks": sorted(stacks, key=str.casefold)}
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def publish_host_sync_files():
    checkmk_base_url = os.environ.get(
        "CHECKMK_BASE_URL", "https://checkmk.example.invalid/mysite"
    ).rstrip("/")
    host_folder = os.environ.get("CHECKMK_HOST_FOLDER", "Server/Docker").strip("/")
    config = {
        "checkmk_base_url": checkmk_base_url,
        "checkmk_host_folder": host_folder,
        "inventory_file": "stacks.json",
        "inventory_max_age_seconds": INVENTORY_MAX_AGE_SECONDS,
        "tls_ca_cert": os.environ.get("CHECKMK_CA_CERT", "").strip() or None,
        "tls_verify": os.environ.get("CHECKMK_TLS_VERIFY", "true").lower()
        not in {"0", "false", "no"},
    }
    atomic_write(HOST_SYNC_DIR / "sync_stack_hosts.py", HOST_SYNC_TEMPLATE.read_text(encoding="utf-8"), 0o755)
    atomic_write(
        HOST_SYNC_DIR / "sync_config.json",
        json.dumps(config, indent=2, sort_keys=True) + "\n",
    )


def parse_docker_time(value):
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def duration_seconds(state, now):
    started = parse_docker_time(state.get("StartedAt", ""))
    if started is None:
        return 0
    if state.get("Status") == "running":
        ended = now
    else:
        ended = parse_docker_time(state.get("FinishedAt", "")) or started
    return max(0, int((ended - started).total_seconds()))


def human_duration(seconds):
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def resource_metrics(stats):
    try:
        cpu = stats["cpu_stats"]
        previous_cpu = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - previous_cpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - previous_cpu["system_cpu_usage"]
        online_cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", [])) or 1
        cpu_percent = max(0.0, cpu_delta / system_delta * online_cpus * 100.0) if system_delta > 0 and cpu_delta > 0 else 0.0

        memory = stats["memory_stats"]
        raw_usage = int(memory.get("usage", 0))
        memory_stats = memory.get("stats", {})
        cache = int(memory_stats.get("total_inactive_file", memory_stats.get("cache", 0)))
        memory_used = max(0, raw_usage - cache)
        memory_limit = int(memory.get("limit", 0))
        return {
            "cpu_percent": cpu_percent,
            "memory_bytes": memory_used,
            "memory_limit_bytes": memory_limit,
        }
    except (KeyError, TypeError, ValueError):
        return None


def healthcheck_configured(inspect):
    test = inspect.get("Config", {}).get("Healthcheck", {}).get("Test", [])
    return bool(test) and test[0].upper() != "NONE"


def safe_service_name(name):
    return re.sub(r"[;~!$%^&*|\\'\"<>?,()=]", "", name).strip() or "unnamed"


def safe_summary(text):
    return " ".join(str(text).replace("\r", " ").replace("\n", " ").split())


def format_local_check(state, service_name, metrics, summary):
    perfdata = "|".join(f"{name}={value};;;;" for name, value in metrics.items()) or "-"
    return f'{state} "{safe_service_name(service_name)}" {perfdata} {safe_summary(summary)}'


def container_name(listed, inspect):
    names = listed.get("Names", [])
    name = names[0].lstrip("/") if names else inspect.get("Name", "unknown").lstrip("/")
    return name or "unknown"


def inspect_container(api, listed, now):
    inspect = api.inspect_container(listed["Id"])
    state = inspect.get("State", {})
    state_name = state.get("Status", listed.get("State", "unknown"))
    name = container_name(listed, inspect)
    runtime = duration_seconds({**state, "Status": state_name}, now)
    is_running = state_name == "running"
    try:
        stats = resource_metrics(api.container_stats(listed["Id"])) if is_running else None
    except DockerApiError as exc:
        logging.warning("Stats unavailable for %s: %s", name, exc)
        stats = None

    health = state.get("Health")
    health_logs = []
    if health:
        for item in health.get("Log", [])[-3:]:
            output = safe_summary(item.get("Output", ""))
            if output:
                health_logs.append(output[:500])

    labels = inspect.get("Config", {}).get("Labels") or listed.get("Labels") or {}
    project = labels.get("com.docker.compose.project", "standalone")
    return {
        "id": listed["Id"],
        "name": name,
        "project": project,
        "state": state_name,
        "exit_code": state.get("ExitCode"),
        "runtime_seconds": runtime,
        "restart_count": int(inspect.get("RestartCount", 0)),
        "health_configured": healthcheck_configured(inspect),
        "health_status": (health or {}).get("Status", "starting"),
        "health_failing_streak": int((health or {}).get("FailingStreak", 0)),
        "health_logs": health_logs,
        "resources": stats,
    }


def collect_stacks(api, blacklist, now=None):
    now = now or datetime.now(timezone.utc)
    stacks = {}
    listed_containers = api.list_containers()
    eligible = []
    for listed in listed_containers:
        labels = listed.get("Labels") or {}
        project = labels.get("com.docker.compose.project", "standalone")
        if project in blacklist:
            continue
        eligible.append((project, listed))

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(inspect_container, api, listed, now): (project, listed)
            for project, listed in eligible
        }
        for future in as_completed(futures):
            project, listed = futures[future]
            try:
                item = future.result()
            except DockerApiError as exc:
                logging.warning("Container vanished or cannot be inspected: %s", exc)
                continue
            stacks.setdefault(project, []).append(item)
    for containers in stacks.values():
        containers.sort(key=lambda item: item["name"].casefold())
    return stacks


def container_state_check(container):
    state = container["state"]
    check_state = 0 if state == "running" else 2
    runtime_label = "uptime" if state == "running" else "last runtime"
    summary = (
        f"{state}; {runtime_label} {human_duration(container['runtime_seconds'])}; "
        f"restarts {container['restart_count']}"
    )
    if state != "running":
        summary += f"; exit code {container['exit_code']}"
    resources = container.get("resources")
    metrics = {
        "runtime_seconds": container["runtime_seconds"],
        "restart_count": container["restart_count"],
    }
    if resources:
        metrics.update(resources)
    else:
        summary += "; resource metrics unavailable"
    return format_local_check(
        check_state,
        f"Container State {container['name']}",
        metrics,
        summary,
    )


def container_health_check(container):
    status = container["health_status"]
    state = {"healthy": 0, "starting": 1, "unhealthy": 2}.get(status, 3)
    detail = "; ".join(container["health_logs"])
    summary = f"{status}; failing streak {container['health_failing_streak']}"
    if detail:
        summary += f"; {detail}"
    metrics = {"health_failing_streak": container["health_failing_streak"]}
    return format_local_check(
        state,
        f"Container Health {container['name']}",
        metrics,
        summary,
    )


def stack_resource_check(containers):
    if not containers:
        return format_local_check(
            2,
            "Stack Resources",
            {"cpu_percent": 0, "memory_bytes": 0},
            "CRITICAL: no Docker containers found for this known stack",
        )

    running = [
        container
        for container in containers
        if container["state"] == "running" and container.get("resources")
    ]
    running_count = sum(container["state"] == "running" for container in containers)
    missing_count = running_count - len(running)
    cpu_percent = sum(item["resources"]["cpu_percent"] for item in running)
    memory_bytes = sum(item["resources"]["memory_bytes"] for item in running)
    state = 1 if missing_count else 0
    summary = (
        f"{len(running)} running containers; CPU {cpu_percent:.1f}% (one core = 100%); "
        f"RAM {memory_bytes} bytes"
    )
    if missing_count:
        summary += f"; metrics unavailable for {missing_count} running container(s)"
    return format_local_check(
        state,
        "Stack Resources",
        {"cpu_percent": round(cpu_percent, 2), "memory_bytes": memory_bytes},
        summary,
    )


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_inventory(stacks, status="ok", error=None):
    inventory = {
        "generated_at": utc_timestamp(),
        "status": status,
        "stacks": sorted(stacks),
    }
    if error:
        inventory["error"] = safe_summary(error)[:500]
    try:
        atomic_write(HOST_SYNC_DIR / "stacks.json", json.dumps(inventory, indent=2) + "\n")
    except OSError:
        logging.exception("Could not update stack inventory in %s", HOST_SYNC_DIR)


def render_agent_output(stacks, source_status="OK", source_detail="inventory collected"):
    lines = [
        "<<<check_mk>>>",
        "Version: 2.4.0",
        "AgentOS: linux",
        "Hostname: docker-exporter",
        "<<<local:sep(0)>>>",
        format_local_check(0 if source_status == "OK" else 2, "Docker Exporter", {}, source_detail),
    ]
    for project in sorted(stacks, key=str.casefold):
        containers = stacks[project]
        lines.extend((f"<<<<{project}>>>>", "<<<local:sep(0)>>>"))
        for container in containers:
            lines.append(container_state_check(container))
            if container["health_configured"]:
                lines.append(container_health_check(container))
        lines.append(stack_resource_check(containers))
        lines.append("<<<<>>>>")
    return "\n".join(lines) + "\n"


def build_agent_output(api=None, config=None):
    config = config or {}
    try:
        if not config:
            config = load_config()
        api = api or DockerApi()
        observed_stacks = collect_stacks(api, config["blacklist"])
        known_stacks = load_known_stacks()
        stacks = merge_known_stacks(observed_stacks, known_stacks, config["blacklist"])
        save_known_stacks(stacks)
        write_inventory(stacks)
        return render_agent_output(stacks, "OK", f"Docker API available; {len(stacks)} stack(s) inventoried")
    except Exception as exc:
        logging.exception("Could not collect Docker data")
        try:
            fallback_stacks = load_known_stacks() - config.get("blacklist", set())
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logging.exception("Could not load known stack inventory")
            fallback_stacks = set()
        stacks = {name: [] for name in sorted(fallback_stacks, key=str.casefold)}
        write_inventory(stacks, "error", str(exc))
        return render_agent_output(stacks, "CRIT", f"Docker API unavailable: {safe_summary(exc)}")


class AgentHandler(socketserver.StreamRequestHandler):
    def handle(self):
        probe = b"__healthcheck__\n"
        readable, _, _ = select.select([self.request], [], [], 0.05)
        if readable and self.request.recv(len(probe), socket.MSG_PEEK).startswith(probe):
            self.request.recv(len(probe))
            self.wfile.write(b"OK\n")
            return
        response = build_agent_output()
        self.wfile.write(response.encode("utf-8"))


class AgentServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    publish_host_sync_files()
    with AgentServer(("0.0.0.0", AGENT_PORT), AgentHandler) as server:
        logging.info("Checkmk agent endpoint listening on port %s", AGENT_PORT)
        server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
