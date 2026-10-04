import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exporter"))

from exporter import collect_stacks, load_known_stacks, merge_known_stacks, render_agent_output, save_known_stacks


def container(name, state="running", health_configured=False, health_status="healthy", resources=None):
    return {
        "id": name,
        "name": name,
        "project": "tvh",
        "state": state,
        "exit_code": 0,
        "runtime_seconds": 120,
        "restart_count": 2,
        "health_configured": health_configured,
        "health_status": health_status,
        "health_failing_streak": 0,
        "health_logs": ["check passed"] if health_configured else [],
        "resources": resources,
    }


class FakeDockerApi:
    def __init__(self):
        self.listed = [
            {"Id": "tvheadend", "Labels": {"com.docker.compose.project": "tvh"}},
            {"Id": "autoheal", "Labels": {"com.docker.compose.project": "tvh"}},
            {"Id": "ignored", "Labels": {"com.docker.compose.project": "monitoring"}},
        ]

    def list_containers(self):
        return self.listed

    def inspect_container(self, container_id):
        has_health = container_id == "tvheadend"
        return {
            "Name": f"/{container_id}",
            "Config": {
                "Labels": {"com.docker.compose.project": "tvh"},
                "Healthcheck": {"Test": ["CMD-SHELL", "true"]} if has_health else {},
            },
            "State": {
                "Status": "running",
                "StartedAt": "2026-10-01T00:00:00Z",
                "Health": {
                    "Status": "healthy",
                    "FailingStreak": 0,
                    "Log": [{"Output": "check passed"}],
                } if has_health else None,
            },
            "RestartCount": 2,
        }

    def container_stats(self, container_id):
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200, "percpu_usage": [100, 100]},
                "system_cpu_usage": 1000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100, "percpu_usage": [50, 50]},
                "system_cpu_usage": 900,
            },
            "memory_stats": {
                "usage": 1000,
                "limit": 4096,
                "stats": {"cache": 100},
            },
        }


class ExporterTests(unittest.TestCase):
    def test_every_container_gets_state_and_health_is_optional(self):
        output = render_agent_output({
            "tvh": [
                container("tvheadend", health_configured=True),
                container("tvheadend-autoheal", health_configured=False),
            ]
        })

        self.assertIn('0 "Container State tvheadend"', output)
        self.assertIn('0 "Container Health tvheadend"', output)
        self.assertIn('0 "Container State tvheadend-autoheal"', output)
        self.assertNotIn('"Container Health tvheadend-autoheal"', output)
        self.assertIn("<<<<tvh>>>>", output)
        self.assertIn("<<<<>>>>", output)

    def test_stack_resource_service_sums_all_running_containers(self):
        output = render_agent_output({
            "tvh": [
                container("tvheadend", health_configured=True, resources={
                    "cpu_percent": 12.5,
                    "memory_bytes": 900,
                    "memory_limit_bytes": 4096,
                }),
                container("tvheadend-autoheal", resources={
                    "cpu_percent": 2.5,
                    "memory_bytes": 100,
                    "memory_limit_bytes": 1024,
                }),
            ]
        })

        self.assertIn('cpu_percent=15.0;;;;|memory_bytes=1000;;;;', output)

    def test_blacklist_filters_projects(self):
        stacks = collect_stacks(FakeDockerApi(), {"monitoring"})

        self.assertEqual(set(stacks), {"tvh"})
        self.assertEqual(len(stacks["tvh"]), 2)
        containers_by_name = {item["name"]: item for item in stacks["tvh"]}
        self.assertFalse(containers_by_name["autoheal"]["health_configured"])

    def test_removed_known_stack_keeps_critical_resource_service(self):
        stacks = merge_known_stacks({}, {"step-ca"}, set())
        output = render_agent_output(stacks)

        self.assertIn("<<<<step-ca>>>>", output)
        self.assertIn('2 "Stack Resources"', output)
        self.assertIn("no Docker containers found for this known stack", output)

    def test_blacklisted_known_stack_is_not_piggybacked(self):
        stacks = merge_known_stacks({}, {"syslogger"}, {"syslogger"})

        self.assertEqual(stacks, {})

    def test_known_stacks_registry_persists_between_reads(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            registry_path = Path(temporary_directory) / "known_stacks.json"
            save_known_stacks({"step-ca", "tvh"}, registry_path)

            self.assertEqual(load_known_stacks(registry_path), {"step-ca", "tvh"})


if __name__ == "__main__":
    unittest.main()
