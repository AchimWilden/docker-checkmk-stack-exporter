#!/usr/bin/env python3

import argparse
import getpass
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("sync_config.json")


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"Checkmk API HTTP {status}: {detail}")
        self.status = status


def read_json(path):
    with path.open("r", encoding="utf-8") as input_file:
        return json.load(input_file)


def parse_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_inventory(config, now=None):
    inventory_path = CONFIG_PATH.parent / config["inventory_file"]
    inventory = read_json(inventory_path)
    if inventory.get("status") != "ok":
        raise RuntimeError(f"Exporter-Inventar ist nicht aktuell: {inventory.get('error', 'Status error')}")
    generated_at = parse_timestamp(inventory["generated_at"])
    age = ((now or datetime.now(timezone.utc)) - generated_at).total_seconds()
    if age < 0 or age > int(config["inventory_max_age_seconds"]):
        raise RuntimeError(f"Exporter-Inventar ist veraltet ({int(age)} Sekunden)")
    stacks = inventory.get("stacks")
    if not isinstance(stacks, list) or any(not isinstance(name, str) or not name for name in stacks):
        raise RuntimeError("Exporter-Inventar enthält keine gültige Stack-Liste")
    return sorted(set(stacks), key=str.casefold)


def folder_id(folder_path):
    parts = [part.strip() for part in folder_path.strip("/").split("/") if part.strip()]
    if not parts:
        return "~"
    safe_parts = []
    for part in parts:
        normalized = re.sub(r"[^a-z0-9_]+", "_", part.casefold()).strip("_")
        if not normalized:
            raise ValueError(f"Ungültiger Ordnername: {part!r}")
        safe_parts.append(normalized)
    return "~" + "~".join(safe_parts)


def select_stacks(stacks, selected_stacks):
    if not selected_stacks:
        return stacks
    requested = set(selected_stacks)
    unknown = requested.difference(stacks)
    if unknown:
        raise ValueError(f"Unbekannte Stacks: {', '.join(sorted(unknown))}")
    return [stack for stack in stacks if stack in requested]


class CheckmkApi:
    def __init__(self, base_url, username, secret, tls_verify=True, tls_ca_cert=None, timeout=15):
        self.api_url = base_url.rstrip("/") + "/check_mk/api/1.0"
        self.username = username
        self.secret = secret
        self.timeout = timeout
        if tls_verify:
            self.context = ssl.create_default_context(cafile=tls_ca_cert)
        else:
            self.context = ssl._create_unverified_context()

    def request(self, method, endpoint, body=None, allow_not_found=False):
        url = self.api_url + endpoint
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.username} {self.secret}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            if allow_not_found and exc.code == 404:
                return None
            raise ApiError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Checkmk nicht erreichbar: {exc.reason}") from exc

    def assert_folder_exists(self, folder):
        encoded_folder = urllib.parse.quote(folder, safe="~")
        self.request("GET", f"/objects/folder_config/{encoded_folder}")

    def host_exists(self, hostname):
        encoded_host = urllib.parse.quote(hostname, safe="")
        result = self.request(
            "GET", f"/objects/host_config/{encoded_host}", allow_not_found=True
        )
        return result is not None

    def create_piggyback_host(self, hostname, folder):
        payload = {
            "attributes": {
                "tag_agent": "no-agent",
                "tag_piggyback": "piggyback",
                "tag_address_family": "no-ip",
            },
            "folder": folder,
            "host_name": hostname,
        }
        self.request("POST", "/domain-types/host_config/collections/all", payload)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Legt fehlende Checkmk-Piggyback-Hosts für Docker-Stacks an."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Hosts tatsächlich anlegen; ohne diese Option wird nur ein Dry-Run ausgeführt.",
    )
    parser.add_argument(
        "--stack",
        action="append",
        dest="selected_stacks",
        metavar="NAME",
        help="Nur diesen Stack synchronisieren; mehrfach verwendbar. Ohne Option werden alle Stacks geprüft.",
    )
    args = parser.parse_args(argv)

    try:
        config = read_json(CONFIG_PATH)
        stacks = load_inventory(config)
        stacks = select_stacks(stacks, args.selected_stacks)
        folder = folder_id(config["checkmk_host_folder"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        return 2

    username = input("Checkmk Automation-User: ").strip()
    secret = getpass.getpass("Checkmk Automation-Secret: ")
    if not username or not secret:
        print("Abbruch: Benutzername und Secret sind erforderlich.", file=sys.stderr)
        return 2

    try:
        api = CheckmkApi(
            config["checkmk_base_url"],
            username,
            secret,
            tls_verify=bool(config.get("tls_verify", True)),
            tls_ca_cert=config.get("tls_ca_cert"),
        )
        api.assert_folder_exists(folder)
        missing = [stack for stack in stacks if not api.host_exists(stack)]
        if not missing:
            print("Alle Stack-Hosts sind bereits vorhanden.")
            return 0

        action = "Würde anlegen" if not args.apply else "Lege an"
        for stack in missing:
            print(f"{action}: {stack} in {config['checkmk_host_folder']}")
            if args.apply:
                api.create_piggyback_host(stack, folder)
        if args.apply:
            print("Hosts wurden angelegt. Prüfe und aktiviere die Änderungen anschließend in Checkmk.")
        else:
            print("Dry-Run: Keine Änderungen vorgenommen. Mit --apply werden die fehlenden Hosts angelegt.")
        return 0
    except (ApiError, RuntimeError, KeyError) as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
