# Docker-Exporter fuer Checkmk

Eigenstaendiges Uebergabepaket: Docker-Stacks als Checkmk-Piggyback-Hosts, Container-State/Health und Ressourcenmetriken. Enthalten sind nur Exporter, Socket-Proxy, manuelles Host-Sync-Skript und Tests.

Startpunkt: [Installationsanleitung](docu/Installation.md). Fachlicher Umfang: [Leistungsbeschreibung](docu/Leistungsbeschreibung.md).

Vor dem Start die Beispielwerte in [docker-compose.yml](docker-compose.yml) anpassen. Die Standard-Portbindung ist absichtlich lokal (`127.0.0.1`), die Checkmk-URL ein ungueltiger Platzhalter. Zugangsdaten und Zertifikate sind nicht enthalten.

```text
docker-compose.yml
exporter/       Dockerfile, Python-Implementierung, config.json
host-sync/      beim Containerstart generierte Skriptkopie/Inventar; Beispiel fuer Known-Stacks-Bootstrap
test/           Unit-Tests
docu/           Installation.md und Leistungsbeschreibung.md
```

Die zusaetzlichen kopierten Entwurfsdokumente im lokalen Exportverzeichnis stammen aus der Referenzanlage und sind nicht Teil des kuratierten Uebergabearchivs. Fuer die Installation gilt diese Anleitung, nicht die lokalen Entwuerfe.