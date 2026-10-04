import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exporter"))

import host_sync


class RecordingApi(host_sync.CheckmkApi):
    def __init__(self):
        self.calls = []

    def request(self, method, endpoint, body=None, allow_not_found=False):
        self.calls.append((method, endpoint, body))


class HostSyncTests(unittest.TestCase):
    def test_folder_path_becomes_checkmk_folder_id(self):
        self.assertEqual(host_sync.folder_id("Server/Docker"), "~server~docker")
        self.assertEqual(host_sync.folder_id("/"), "~")

    def test_stack_filter_limits_pilot_and_rejects_unknown_names(self):
        self.assertEqual(
            host_sync.select_stacks(["evcc", "tvh"], ["tvh"]),
            ["tvh"],
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            host_sync.select_stacks(["evcc", "tvh"], ["missing"])

    def test_new_stack_host_is_piggyback_only_without_ip(self):
        api = RecordingApi()
        api.create_piggyback_host("tvh", "~server~docker")

        method, endpoint, payload = api.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(endpoint, "/domain-types/host_config/collections/all")
        self.assertEqual(payload["host_name"], "tvh")
        self.assertEqual(payload["folder"], "~server~docker")
        self.assertEqual(payload["attributes"]["tag_agent"], "no-agent")
        self.assertEqual(payload["attributes"]["tag_piggyback"], "piggyback")
        self.assertEqual(payload["attributes"]["tag_address_family"], "no-ip")
        self.assertNotIn("ipaddress", payload["attributes"])

    def test_inventory_rejects_stale_or_failed_data(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            inventory_path = directory / "stacks.json"
            config_path = directory / "sync_config.json"
            config_path.write_text(
                json.dumps({
                    "inventory_file": "stacks.json",
                    "inventory_max_age_seconds": 60,
                }),
                encoding="utf-8",
            )
            config = host_sync.read_json(config_path)
            now = datetime.now(timezone.utc)
            inventory_path.write_text(
                json.dumps({
                    "generated_at": (now - timedelta(seconds=120)).isoformat(),
                    "status": "ok",
                    "stacks": ["tvh"],
                }),
                encoding="utf-8",
            )
            with patch.object(host_sync, "CONFIG_PATH", config_path):
                with self.assertRaisesRegex(RuntimeError, "veraltet"):
                    host_sync.load_inventory(config, now=now)

    def test_inventory_returns_sorted_unique_stack_names(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            inventory_path = directory / "stacks.json"
            config_path = directory / "sync_config.json"
            config_path.write_text(
                json.dumps({
                    "inventory_file": "stacks.json",
                    "inventory_max_age_seconds": 300,
                }),
                encoding="utf-8",
            )
            config = host_sync.read_json(config_path)
            inventory_path.write_text(
                json.dumps({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "status": "ok",
                    "stacks": ["tvh", "evcc", "tvh"],
                }),
                encoding="utf-8",
            )
            with patch.object(host_sync, "CONFIG_PATH", config_path):
                self.assertEqual(host_sync.load_inventory(config), ["evcc", "tvh"])


if __name__ == "__main__":
    unittest.main()
