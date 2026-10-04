# Leistungsbeschreibung: Docker-Exporter fuer Checkmk

## Zweck

Die Loesung bildet Docker-Compose-Projekte als eigene Checkmk-Hosts ab. Ein zentraler Exporter liefert die Daten aller nicht ausgeschlossenen Projekte ueber Piggyback. Ein vorhandener Checkmk-System-Agent bleibt unabhaengig; pro Container wird kein eigener Agent installiert.

## Lieferumfang

- Docker-Exporter mit Agent-Pull-Endpunkt und Checkmk-Local-Check-Ausgabe.
- Eingeschraenkter Docker-Socket-Proxy als getrennte Komponente.
- Eigenstaendiges Compose fuer diese beiden Dienste.
- JSON-Blacklist auf Compose-Projektebene.
- Manuell gestartetes Skript zur Anlage fehlender Stack-Hosts per Checkmk-REST-API.
- Installationsanleitung und acht fokussierte Unit-Tests.

Checkmk-Server, Zertifikatsstelle, Reverse-Proxy und Benachrichtigungsdienste sind nicht Bestandteil des Pakets. Zugangsdaten, private Schluessel und Daten der Referenzanlage werden nicht mitgeliefert.

## Monitoring

| Service | Inhalt |
|---|---|
| Container State | Docker-Zustand, Uptime bzw. letzte Laufzeit, Restart-Zaehler, verfuegbare CPU-/RAM-Metriken. |
| Container Health | Nur bei Docker-Healthcheck: Health-Status, Fehlerzaehler und verfuegbare Diagnoseausgabe. |
| Stack Resources | CPU-/RAM-Summen aller laufenden Container des Projekts. |
| Docker Exporter | Status der Datenerfassung am Quellhost. |

Neue Projekte und Container werden automatisch inventarisiert. Hostanlage und Checkmk-Serviceuebernahme bleiben bewusst manuelle Schritte. Ohne Compose-Projektlabel werden Container dem gemeinsamen Projekt `standalone` zugeordnet.

Gestoppte, noch vorhandene Container behalten ihren State-Service und melden CRITICAL. Bekannte Stack-Namen bleiben persistent im Host-Mount. Sind alle Container eines bekannten Stacks entfernt, bleibt dessen Piggyback-Host erhalten und `Stack Resources` meldet CRITICAL. Einzelne entfernte Container werden nicht als eigene Services gespeichert. Bewusst stillgelegte Stacks koennen auf die Blacklist gesetzt werden. Healthchecks laufen in den ueberwachten Containern; der Exporter fuehrt keine eigenen HTTP-/TCP-Funktionstests aus.

## Host-Synchronisierung

Der Container stellt die aktuelle Skriptversion und ein timestamp-basiertes Inventar in einem Host-Mount bereit. Das Skript fragt ein Automation-Secret interaktiv ab, arbeitet standardmaessig als Dry-Run und legt mit `--apply` nur fehlende Hosts an. Es aendert und loescht keine bestehenden Hosts und aktiviert keine offenen Checkmk-Aenderungen. Ein Stack-Filter erlaubt eine Pilotinstallation.

## Sicherheit und Betrieb

Nur der Socket-Proxy hat direkten Zugriff auf den Docker-Socket. Der Exporter greift im internen Docker-Netz ueber dessen API zu. Der Proxy sperrt schreibende Methoden, bietet aber weiterhin Zugriff auf Container-Metadaten; das ist keine vollstaendige Isolation vom Dockerhost.

Der Agent-Endpunkt ist unverschluesseltes Legacy-Pull-TCP und muss per Firewall auf Checkmk beschraenkt werden. Die REST-API-Verbindung des manuellen Skripts prueft dagegen standardmaessig TLS-Zertifikate. Secrets werden nicht gespeichert. Das Image laeuft als UID 1000, der Proxy besitzt fuer den Socket erforderliche Privilegien.

## Nachgewiesener Stand

Die Referenzinstallation wurde mit Checkmk Community 2.4.0p27 betrieben: Quellhostabfrage, Stack-Hostanlage und Service-Erkennung mit State, Health und Ressourcen waren erfolgreich. Die exportierte Kopie wird mit elf Unit-Tests sowie Compose-Validierung geprueft. Andere Checkmk-Versionen und Unternehmens-Sicherheitsanforderungen muessen vor Einsatz separat bewertet werden.

Installationsschritte, Parameter, Proxy-Berechtigungen und bekannte Grenzen stehen in [Installation.md](Installation.md).