# Installation: Docker-Exporter fuer Checkmk

## Voraussetzungen

- Linux-Dockerhost mit Docker Engine und Docker Compose v2 oder neuer.
- Netzwerkzugriff zum Image-Registry fuer den Build.
- Checkmk mit erreichbarem Agent-Pull-Zugriff auf den Dockerhost. Erprobt mit Checkmk Community 2.4.0p27.
- Python 3.10 oder neuer auf dem Dockerhost fuer das manuelle Host-Sync-Skript; keine pip-Pakete erforderlich.
- Checkmk-Automationsbenutzer mit Leserechten fuer Ordner/Hosts und Rechten zur Hostanlage. Ein WebUI-Passwort ist kein Automation-Secret.
- Eindeutige Compose-Projektnamen, die nicht mit vorhandenen Checkmk-Hosts anderer Systeme kollidieren.

Die Loesung braucht keinen Checkmk-Agent auf dem Dockerhost. Ein bereits vorhandener System-Agent bleibt unveraendert. Checkmk, Caddy, eine PKI sowie Signal-Dienste sind nicht Teil dieses Pakets.

## Komponenten und Socket-Proxy

Das Compose startet genau zwei Dienste:

| Dienst | Aufgabe |
|---|---|
| `docker-checkmk-exporter` | Liest Docker-Zustaende, Healthchecks und Ressourcen; liefert Agent-Output mit Local Checks und Piggyback; stellt das Host-Sync-Skript bereit. |
| `docker-checkmk-socket-proxy` | Vermittelt den lesenden Docker-API-Zugriff und blockiert schreibende API-Methoden. |

```text
Checkmk -> Dockerhost:8656 -> Exporter:6556
                              |
                              v
                         Socket-Proxy:2375 -> /var/run/docker.sock
```

Nur der Proxy mountet den Host-Docker-Socket. Der Exporter hat keinen direkten Socket-Zugriff. Im internen Netz `docker-api` sind nur Proxy und Exporter; Proxy-Port 2375 wird nicht am Host veroeffentlicht. Der Exporter hat zusaetzlich das Netz `docker-agent`, damit Docker dessen Agent-Port veroeffentlichen kann.

`CONTAINERS=1` erlaubt die Container-API fuer Inventar, Inspect und Stats. `POST=0` laesst laut Proxy nur GET/HEAD zu; Starten, Stoppen, Entfernen oder Erstellen von Containern ist damit blockiert. `EVENTS`, `INFO`, `PING` und `VERSION` sind hier explizit deaktiviert, weil der Exporter sie nicht braucht. Andere API-Bereiche bleiben nach Proxy-Defaults gesperrt.

Wichtig: Ein Socket-Mount mit `:ro` allein macht Docker-API-Aufrufe nicht zu Leseoperationen. Die Einschraenkung erfolgt durch den Proxy. Der Proxy selbst besitzt weiterhin hochprivilegierten Socket-Zugriff. `CONTAINERS=1` ist keine pfadgenaue Minimalfreigabe: Lesbare Containerinformationen koennen auch sensible Umgebungswerte enthalten. Proxy-Netz und Images sind daher sicherheitsrelevant; keine weiteren Dienste an `docker-api` anschliessen und Port 2375 niemals extern freigeben.

## Paket vorbereiten

Archiv auf dem Dockerhost in ein eigenes Verzeichnis entpacken und dort arbeiten. Alle folgenden Shell-Befehle werden auf dem Dockerhost im Paketverzeichnis ausgefuehrt, nicht auf dem Checkmk-Server.

```bash
mkdir -p host-sync
```

Das Image laeuft mit UID/GID `1000:1000`. Das Verzeichnis `host-sync` muss fuer diese UID beschreibbar sein. Bei abweichendem Besitzer gezielt anpassen:

```bash
sudo chown 1000:1000 host-sync
sudo chmod 0750 host-sync
```

Nicht pauschal `chmod 777` verwenden. Im Mount liegen spaeter Skript, nicht geheime Verbindungskonfiguration und Stack-Inventar, aber keine API-Secrets.

## Compose konfigurieren

Die Einstellungen stehen direkt in der Paket-Compose-Datei. Eine `.env` ist nicht erforderlich.

| Einstellung | Anpassung |
|---|---|
| `CHECKMK_BASE_URL` | Echte HTTPS-Site-URL, z. B. `https://monitoring.example.org/mysite`, ohne `/check_mk`. Der Platzhalter `example.invalid` ist nicht betriebsbereit. |
| `CHECKMK_HOST_FOLDER` | Interner Checkmk-Ordnerpfad, z. B. `server/docker`. Gross-/Kleinschreibung der Anzeige wird im Skript normalisiert; die internen Ordnernamen muessen dazu passen. Ordner vorher anlegen. |
| `CHECKMK_CA_CERT` | Leer fuer systemweit vertraute CA; bei interner PKI absoluter Pfad zur oeffentlichen CA-PEM-Datei auf dem Dockerhost. |
| `CHECKMK_TLS_VERIFY` | `true` beibehalten. |
| `INVENTORY_MAX_AGE_SECONDS` | Maximales Inventaralter vor Host-Sync, Standard 900 Sekunden. |
| `ports.host_ip` | Von Checkmk erreichbare Management-IP des Dockerhosts. Der sichere Lieferdefault `127.0.0.1` erlaubt nur lokale Tests. |
| `ports.published` | Freier Host-Port; Standard 8656, damit ein vorhandener System-Agent auf 6556 nicht kollidiert. |

Der CA-Pfad wird dem auf dem Host gestarteten Skript uebergeben. Die Datei muss dort lesbar sein; der Exporter benoetigt dafuer keinen zusaetzlichen Zertifikats-Mount. Keine privaten CA-Schluessel mitgeben. Hostname, DNS-Aufloesung und Zertifikat-SAN muessen zusammenpassen.

Der Compose-Projektname ist `docker-checkmk`. Bei mehreren Installationen auf demselben Dockerhost muss auch die Host-Portbindung eindeutig sein.

## Blacklist

In `exporter/config.json` ausgeschlossene Compose-Projektnamen eintragen:

```json
{
  "blacklist": ["debug-stack"]
}
```

Eine leere Liste ueberwacht alle Projekte, einschliesslich des Exporter-Projekts. Einzelne Container-Namen sind keine Blacklist-Projektnamen. Die Konfiguration wird bei der Agent-Abfrage neu eingelesen. Ohne Compose-Label werden Container unter dem gemeinsamen Stack `standalone` ausgegeben.

Beim ersten Agent-Abruf schreibt der Exporter bekannte, nicht blacklistete Projekte nach `host-sync/known_stacks.json`. Die Liste bleibt ueber Container-Neustarts erhalten. Wenn bereits vor der allerersten Abfrage ein erwarteter Stack keine Container mehr hat, kann die Liste vor dem Start aus `host-sync/known_stacks.example.json` als `known_stacks.json` angelegt und mit den erwarteten Projektnamen befuellt werden.

## Start und lokaler Test

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail 30 docker-checkmk-exporter
```

Der erste Docker-Healthcheck kann etwa 30 Sekunden brauchen. Er prueft den Agent-Prozess, nicht die gesamte Docker-Datensammlung. Deren Fehler erscheinen im Checkmk-Service `Docker Exporter`.

Agent auf dem Host pruefen (Adresse bei geaenderter Portbindung anpassen):

```bash
nc -w 30 127.0.0.1 8656
```

Erwartet werden `<<<check_mk>>>`, `<<<local:sep(0)>>>` und `<<<<projektname>>>>`-Bloecke. Mit der produktiven Host-IP denselben Test vom Checkmk-Server ausfuehren.

Der Endpunkt ist ein unverschluesselter Legacy-Pull-Agent ohne TLS-Controller oder Benutzeranmeldung. Zugriff mittels Netzwerkfirewall auf die Checkmk-Serveradresse begrenzen. Eine Bindung an die Management-IP allein ist keine Zugriffskontrolle. Fuer Netze mit verpflichtender Transportverschluesselung ist dieser Endpunkt ohne zusaetzlichen gesicherten Transport nicht geeignet.

## Checkmk einrichten

1. Quellhost `docker-exporter` mit der Management-IP des Dockerhosts anlegen. Checkmk-Agent aktiv, SNMP aus.
2. Unter Agent-Zugriffsregeln die Regel `TCP port for connection to Checkmk agent` auf den veroeffentlichten Port setzen, ausschliesslich fuer `docker-exporter`.
3. Falls Agent-Datenverschluesselung erzwungen wird, nur fuer diese Legacy-Datenquelle die passende Ausnahme einrichten; keine globalen Sicherheitsregeln abschalten.
4. Aenderungen aktivieren, Quellhost abfragen und seine Services uebernehmen. Er hat `Check_MK Agent` und `Docker Exporter`; Container-Services gehoeren zu den Stack-Hosts.
5. Zielordner fuer Stack-Hosts anlegen. Quellhost ausserhalb dieses Ordners halten, damit er keine No-IP-Regeln erbt.
6. Fuer Stack-Hosts die Regel `Host check command` auf `Always assume host is UP` begrenzen. Fehler werden ueber Piggyback und Services gemeldet, nicht ueber Ping.

## Stack-Hosts synchronisieren

Das Skript wird vom Container beim Start atomar nach `host-sync` kopiert. Nicht diese generierte Kopie bearbeiten; Quellversion liegt in `exporter/host_sync.py`.

Erst einen real vorhandenen Stack pruefen, z. B. `web` (durch eigenen Projektnamen ersetzen):

```bash
python3 host-sync/sync_stack_hosts.py --stack web
python3 host-sync/sync_stack_hosts.py --stack web --apply
```

Das Skript fragt Automation-User und Automation-Secret interaktiv ab; Secret-Eingabe ist unsichtbar. Nicht in Befehlszeilen oder Dateien speichern. Ohne `--apply` erfolgen nur Lesezugriffe. Vorhandene Hosts werden nicht veraendert, umbenannt oder geloescht.

Nach dem Pilot alle fehlenden Stack-Hosts pruefen und anlegen:

```bash
python3 host-sync/sync_stack_hosts.py
python3 host-sync/sync_stack_hosts.py --apply
```

Die Anlage verwendet `no-agent`, `piggyback` und `no-ip`. Aenderungen in Checkmk manuell pruefen und aktivieren; beim Automationsbenutzer gegebenenfalls die GUI-Checkbox fuer Aenderungen anderer Benutzer aktivieren.

Anschliessend im Zielordner Bulk-Service-Erkennung ausfuehren, gefundene Services uebernehmen und erneut aktivieren. Ohne Service-Erkennung kann Checkmk fuer No-IP-Hosts einen unbrauchbaren PING-Service anzeigen. Die Host-UP-Regel allein ersetzt die Service-Erkennung nicht.

Pro Container erscheint State, bei konfiguriertem Docker-Healthcheck zusaetzlich Health. Pro Stack erscheint `Stack Resources`. Ein noch vorhandener gestoppter Container meldet CRITICAL im State-Service. Wenn alle Container eines bekannten Stacks entfernt wurden, bleibt der Piggyback-Host durch die persistente Known-Stack-Liste erhalten und `Stack Resources` meldet CRITICAL. Einzelne entfernte Container werden nicht als eigene Services gespeichert. Bewusst stillgelegte Projekte auf die Blacklist setzen.

## Betrieb und Fehlersuche

| Fehler | Pruefung |
|---|---|
| Kein Agent erreichbar | Management-IP, Portregel, Firewall, `docker compose ps` pruefen. |
| Docker-API-Fehler / HTTP 403 | Proxy-Status, `CONTAINERS=1`, `POST=0`, Socket-Mount und internes Netz pruefen. Nicht mit `privileged` oder externem Proxy-Port umgehen. |
| Keine Sync-Dateien | Schreibrechte fuer UID 1000 und Exporter-Startlogs pruefen. |
| Inventar veraltet | Quellhost-Agent erneut abfragen. Inventar wird bei echten Agent-Abfragen aktualisiert, nicht durch den Docker-Health-Probe. |
| API HTTP 401 | Exakten Automationsbenutzer und Automation-Secret statt WebUI-Passwort verwenden. |
| API HTTP 404 folder | Interne Ordnernamen statt nur Anzeigenamen pruefen. Das Skript erzeugt `~server~docker` aus `Server/Docker`. |
| TLS-Fehler | CA-Datei auf dem Host, SAN, DNS und Site-URL pruefen; Verifikation nicht pauschal abschalten. |
| Host DOWN / PING UNKNOWN | No-IP-Hostpruefung und Service-Erkennung kontrollieren. |

Updates: Paketquellen austauschen, dann `docker compose up -d --build`. Die Skriptkopie und Verbindungskonfiguration werden beim Exporterstart aktualisiert. Bestehende Checkmk-Hosts bleiben unveraendert. Stoppen: `docker compose stop`; bestehende Monitoringdaten werden dann veraltet.

Die CPU-Skala ist ein Kern = 100 Prozent; mehrkernige Nutzung kann ueber 100 Prozent liegen. RAM-Werte sind Bytes mit einer Docker-Stats-basierten Cache-Bereinigung. Das ausgegebene Memory-Limit kann ohne explizite Containerbegrenzung dem Host-Limit entsprechen. Keine CPU-/RAM-Alarm-Schwellen sind voreingestellt. Health-Ausgaben werden gekuerzt und auf eine Zeile reduziert.

## Tests und Grenzen

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s test -p 'test_*.py' -v
```

Die automatisierten Tests pruefen Exportformat, State/Health-Trennung, Blacklist, Stack-Summen, persistente Stack-Erkennung, CRITICAL bei fehlenden Stack-Containern, Inventaralter und Hostanlage-Payload. Sie ersetzen nicht die Service-Erkennung und Alarmpruefung in der eigenen Checkmk-Installation. Die Software ist eine erste funktionsfaehige Version, kein vollstaendig gehaertetes Produkt: einzelne entfernte Container werden nicht persistent gehalten, Checkmk-Aenderungen nicht automatisch aktiviert und der Agent-Port nicht mit TLS abgesichert.