# Docker-Exporter fuer Checkmk

Der Exporter ueberwacht Docker-Compose-Projekte mit Checkmk, ohne in jedem Container einen Agenten zu installieren. Ein zentraler Agent-Endpunkt liefert die Containerdaten und ordnet sie per Piggyback den Checkmk-Hosts der Compose-Projekte zu. Ein bereits installierter System-Agent bleibt davon unabhaengig.

## Was ueberwacht wird

| Checkmk-Service | Inhalt |
| --- | --- |
| `Container State` | Zustand jedes vorhandenen Containers, Laufzeit, Restart-Zaehler sowie verfuegbare CPU- und RAM-Metriken. Gestoppte Container melden CRITICAL. |
| `Container Health` | Health-Status, Fehlerzaehler und Diagnoseausgabe, aber nur wenn der Container einen Docker-Healthcheck besitzt. |
| `Stack Resources` | CPU- und RAM-Summen der laufenden Container eines Compose-Projekts. Ein bekannter Stack ohne Container bleibt sichtbar und meldet CRITICAL. |
| `Docker Exporter` | Status der Datenerfassung am Checkmk-Quellhost. |

Neue Compose-Projekte und Container werden automatisch inventarisiert. Die Checkmk-Hosts fuer Stacks werden bewusst manuell angelegt; ebenso muessen die Services in Checkmk erkannt und uebernommen werden. Der Stack-Name entspricht dem Compose-Projektnamen. Projekte ohne Compose-Label werden dem gemeinsamen Stack `standalone` zugeordnet. Ausgeschlossene Projekte werden in `exporter/config.json` auf eine Blacklist gesetzt.

Das Host-Sync-Skript legt ausschliesslich fehlende Hosts an. Es startet standardmaessig als Dry-Run, fragt das Checkmk-Automations-Secret verdeckt ab und speichert es nicht. Bestehende Hosts werden nicht geaendert oder geloescht.

## Architektur

```text
Checkmk -- Agent-Pull --> Exporter -- lesende Docker-API --> Socket-Proxy --> Docker-Socket
	|
	+-- Piggyback-Daten --> Checkmk-Hosts der Compose-Projekte
```

Das Paket besteht aus Exporter und Socket-Proxy. Nur der Proxy mountet den Docker-Socket; schreibende API-Methoden sind deaktiviert. Der Proxy kann dennoch Container-Metadaten lesen, darunter potenziell sensible Umgebungswerte. Den Proxy-Port 2375 niemals am Host veroeffentlichen oder weitere Dienste an das interne API-Netz anschliessen.

## Voraussetzungen

- Linux-Dockerhost mit Docker Engine und Docker Compose v2 oder neuer.
- Checkmk mit Netzwerkzugriff auf den Agent-Endpunkt des Dockerhosts. Erprobt mit Checkmk Community 2.4.0p27.
- Python 3.10 oder neuer auf dem Dockerhost fuer das Host-Sync-Skript; keine zusaetzlichen Python-Pakete erforderlich.
- Checkmk-Automationsbenutzer mit Rechten zum Lesen und Anlegen von Hosts.
- Eindeutige Compose-Projektnamen, die nicht mit anderen Checkmk-Hosts kollidieren.

Checkmk-Server, Zertifikatsstelle, Reverse-Proxy und Zugangsdaten sind nicht Bestandteil dieses Projekts.

## Schnellstart

Repository auf dem Dockerhost auschecken und in das Projektverzeichnis wechseln:

```bash
git clone https://github.com/AchimWilden/docker-checkmk-stack-exporter.git
cd docker-checkmk-stack-exporter
```

1. In [docker-compose.yml](docker-compose.yml) die echte `CHECKMK_BASE_URL`, den Zielordner `CHECKMK_HOST_FOLDER`, bei Bedarf `CHECKMK_CA_CERT` sowie `ports.host_ip` und `ports.published` konfigurieren. `CHECKMK_TLS_VERIFY` aktiviert lassen. Der Lieferdefault bindet den Agent-Port nur an `127.0.0.1` und ist daher nicht direkt von einem entfernten Checkmk-Server erreichbar. Port 8656 ist der Host-Port; intern lauscht der Exporter auf 6556.
2. Das Host-Verzeichnis fuer Inventar und Sync-Skript fuer UID/GID `1000:1000` beschreibbar machen. Keine pauschalen `chmod 777` verwenden:

	```bash
	mkdir -p host-sync
	sudo chown 1000:1000 host-sync
	sudo chmod 0750 host-sync
	```

3. Compose pruefen und die Dienste starten:

	```bash
	docker compose config --quiet
	docker compose up -d --build
	docker compose ps
	```

4. In Checkmk einen Agent-Quellhost fuer den Dockerhost einrichten und auf den konfigurierten Host-Port verbinden. Den Agent-Zugriff auf diesen Port per Firewall auf den Checkmk-Server begrenzen. Anschliessend den Quellhost abfragen, damit Exporter-Skript und Stack-Inventar geschrieben werden.
5. Einen Stack zuerst als Dry-Run pruefen und dann bei Bedarf anlegen:

	```bash
	python3 host-sync/sync_stack_hosts.py --stack <compose-projekt>
	python3 host-sync/sync_stack_hosts.py --stack <compose-projekt> --apply
	```

	Danach die Aenderungen in Checkmk pruefen und aktivieren sowie im Stack-Zielordner die Service-Erkennung ausfuehren und Services uebernehmen.

Die Standard-Checkmk-URL ist absichtlich ein ungueltiger Platzhalter. TLS-Pruefung nicht abschalten; bei einer internen PKI den oeffentlichen CA-Pfad konfigurieren. Zugangsdaten oder private Schluessel gehoeren nicht ins Repository.

## Sicherheit und Grenzen

Der Agent-Endpunkt verwendet unverschluesseltes Legacy-Pull-TCP ohne Anmeldung. Er ist nur fuer vertrauenswuerdige Netze geeignet und muss per Firewall auf den Checkmk-Server beschraenkt werden. Die Verbindung des Host-Sync-Skripts zur Checkmk-REST-API prueft TLS-Zertifikate standardmaessig.

Ein Docker-Healthcheck wird ausgewertet, aber der Exporter fuehrt keine eigenen HTTP- oder TCP-Funktionstests in den Containern aus. Einzelne entfernte Container werden nicht dauerhaft als Services gespeichert. Bekannte Stack-Namen bleiben im Host-Mount erhalten; wenn alle Container eines bekannten Stacks entfernt wurden, meldet dessen `Stack Resources`-Service CRITICAL. Bewusst stillgelegte Projekte daher auf die Blacklist setzen.

Es sind keine CPU- oder RAM-Alarmschwellen voreingestellt. CPU kann bei mehrkerniger Nutzung ueber 100 Prozent liegen. Die Software ersetzt weder Service-Erkennung noch Alarmpruefung in der eigenen Checkmk-Installation.

## Entwicklung und Dokumentation

Unit-Tests aus dem Repository-Stammverzeichnis ausfuehren:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s test -p 'test_*.py' -v
```

Die [Installationsanleitung](docu/Installation.md) beschreibt Checkmk-Regeln, Proxy-Berechtigungen, Blacklist, Betrieb und Fehlersuche im Detail. Die [Leistungsbeschreibung](docu/Leistungsbeschreibung.md) fasst Monitoringumfang, Host-Synchronisierung und Betriebsgrenzen zusammen.