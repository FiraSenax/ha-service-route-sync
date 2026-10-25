# Service Route Sync

[Architektur, Sicherheitsgrenzen und Kompatibilität](docs/ARCHITECTURE.md) · [Sicherheitsrichtlinie](SECURITY.md)

Deutsch · [English](README.md)

[![Tests](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml)

Eine Home-Assistant-Integration, die Zielnetze ausgewählter Spiele und Dienste in bestehenden UniFi-Routen aktuell hält. Läuft direkt in HA, nutzt einen eigenen, ausdrücklich konfigurierten lokalen UniFi-API-Schlüssel und braucht keinen externen Timer.

**Vorschau ist standardmäßig aktiv.** Controller-Zugriff, Router-Schreibvorgänge und Wiederherstellung sind automatisiert mit simuliertem Backend getestet. Diese Version muss vor automatischen Schreibzugriffen noch am eigenen Controller geprüft werden.

**Upgrade auf 0.3:** Eigene Zugangsdaten, Steuerungs-Helper, Diagnoseprofil und Benachrichtigungsziel eintragen. Persönliche Standardwerte werden nicht übernommen. Details stehen in der [Konfigurations- und Umstellungsanleitung](docs/CONFIGURATION.md).

## Dienste

| Auswahl | Datenquelle und Umfang |
| --- | --- |
| Call of Duty / Demonware | RIPE, AS60229 |
| Blizzard / Battle.net | RIPE, AS57976 |
| Riot Games | RIPE, AS6507 |
| PUBG / Krafton | RIPE, AS18321 |
| Destiny / Bungie | RIPE, AS394073 |
| i3D.net | RIPE, AS49544; gemeinsames Hosting mehrerer Spiele |
| Microsoft 365 | Öffentliche IP-Endpunkte aller M365-Servicebereiche, ausgewählte Kategorien |
| Teams, Exchange, SharePoint / OneDrive | Gewählter Servicebereich einschließlich `Common`, ausgewählte Kategorien |
| Azure | Exakt ausgewählte Service Tags, beispielsweise `Storage.WestEurope` |
| AWS | Exakt ausgewählte Regionen und Dienste, beispielsweise `eu-central-1` und `S3` |
| SAP | Eigene, dokumentierte **Zielnetze** der tatsächlich verwendeten SAP-Dienste; [Cloud-ALM-Hinweise](docs/SAP_DESTINATIONS.md) |
| Eigene Dienste | Öffentliche IPv4-/IPv6-Zielnetze als CIDR |

Die Spieleauswahl deckt bekannte Betreiber-ASNs ab. Spiele können zusätzliche Server in fremden Clouds, CDNs oder Netzen nutzen. Es ist keine vollständige Spieleerkennung. Microsoft-Endpunkte ohne veröffentlichte IP-Adresse werden nicht abgebildet. SAP-Egress-/NAT-Adressen sind nicht automatisch die passenden Zielnetze für Clientzugriffe.

Für Microsoft 365 ist standardmäßig die Kategorie **Optimize** ausgewählt; **Allow** und **Default** können zusätzlich gewählt werden.

Azure und AWS werden bewusst nach konkreten Tags, Diensten und Regionen ausgewählt. Die gesamte Cloud zu routen kann viele fremde Anwendungen betreffen. Überschneidende Dienste, etwa M365 und Teams, benötigen eine bewusste Routenpriorität und Gerätezuordnung im Router.

## Router

| Backend | Stand |
| --- | --- |
| UniFi Network | Implementiert; eigene lokale UniFi-Zugangsdaten erforderlich |
| FRITZ!Box | Noch nicht implementiert; keine gleichwertige API-Funktion zugesagt |
| Weitere Router | Backend-Schnittstelle vorhanden, konkrete Unterstützung noch offen |

Die Integration aktualisiert ausschließlich `ip_addresses` ausdrücklich zugeordneter IP-Routen. Sie legt keine Routen an und löscht keine. WAN, Quellgeräte/VLANs, Aktivierung, Kill Switch und vorhandene IP-Bereichsanker bleiben erhalten. Eine deaktivierte Routerroute bleibt deaktiviert.

Bei überwachten Clients zeigt **Verbindungstyp** an, ob ein Gerät per Kabel,
WLAN oder derzeit nicht verbunden ist. Die Diagnose-Sensoren für
Portgeschwindigkeit und Portzähler liefern nur bei einem **aktuell belegten
kabelgebundenen Switchport** Werte. Bei WLAN-Geräten bleiben sie bewusst „nicht
verfügbar“; die WLAN-Verbindung hat keinen eigenen Switchport mit solchen
Zählern. Ein vorübergehend fehlerhafter UniFi-API-Abruf verwirft außerdem
keinen zuletzt gültigen Client-Snapshot.

## Installation

Home Assistant **2026.9 oder neuer** und ein erreichbarer **UniFi Network**-Controller werden benötigt. Die Einrichtung erfolgt ausschließlich mit einem lokalen Network-API-Schlüssel. Benötigte Zugriffe und Grenzen der Rechtevergabe stehen unter [Berechtigungen](docs/PERMISSIONS.md). Für Beobachtung werden nur Lesezugriffe benötigt; Routenänderungen brauchen zusätzlich Updates vorhandener Routen. Ein separater Schlüssel ist nicht automatisch auf diese Rechte beschränkt: Entscheidend ist, welche Beschränkungen die eingesetzte UniFi-Version tatsächlich durchsetzt.

1. HACS öffnen → Menü → **Benutzerdefinierte Repositories**.
2. `https://github.com/FiraSenax/ha-service-route-sync` hinzufügen, Typ **Integration**.
3. **Service Route Sync** herunterladen und HA neu starten.
4. Einstellungen → Geräte & Dienste → Integration hinzufügen → **Service Route Sync**.
5. Controller-Host, Port, Site und eigene lokale Zugangsdaten eingeben; Spiele/Dienste und Cloud-Filter auswählen.
6. Jedem Dienst eine eigene vorhandene IP-Route zuordnen. Diese Routen zuvor im UniFi-Gateway mit gewünschtem WAN und Geräte-/Netzbereich anlegen.

Das Repository ist als HACS-Custom-Repository installierbar; eine Aufnahme in den HACS-Standardkatalog ist nicht erfolgt.
Der [Aufnahme-PR](https://github.com/hacs/default/pull/11881) wartet auf die Maintainer.

## Bedienung

Nach der Einrichtung erscheinen:

- **Status**: `preview`, `current` oder `review_required`.
- **Destination prefixes**: Anzahl der ausgewählten Zielpräfixe.
- **Routes with changes**: Routen mit Änderungen; Attribute zeigen je Dienst Präfixzahl sowie hinzugefügte/entfallende Einträge.
- **Last applied change**: Zeitpunkt einer vollständig verifizierten Änderung.
- **Refresh destinations**: Daten und Vorschau neu prüfen; bei eingeschalteter Automatik auch Änderungen anwenden.
- **Automatic route updates**: Standardmäßig **aus**. Einschalten erlaubt Änderungen und startet eine frische Prüfung.
- **Restore last route update**: Letzte einzelne Routensicherung zurückspielen, wenn die Automatik aus ist und niemand die Route inzwischen verändert hat.

Vor dem Einschalten externe Skripte oder weitere Integrationen deaktivieren, die dieselben Routen ändern. Pro Controller-Endpunkt und Site wird eine Service-Route-Sync-Instanz eingerichtet.

Das Prüfintervall beträgt standardmäßig 24 Stunden, einstellbar zwischen einer Stunde und einer Woche. Erfolgreich geladene Quellen werden eine Stunde zwischengespeichert; erneutes Drücken lädt in dieser Zeit dieselben Quelldaten, liest den Router aber erneut. Änderungen der Integrationsoptionen schalten die Automatik wieder aus.

## Prüfungen und Wiederherstellung

Alle ausgewählten Quellen und Routen werden vor dem ersten Schreibzugriff geprüft. Leere/fehlerhafte Quellen, falsche ASN-Inhaber, private Netze, Default-Routen, Portfilter und überschrittene Präfixlimits stoppen den Lauf. Listen werden niemals abgeschnitten.

Die Vorschau zeigt auch große Änderungen. Automatisches Anwenden ist standardmäßig blockiert, wenn mehr als 50 % der bisherigen Präfixeinträge entfallen. Die Grenze ist einstellbar; sie zählt CIDR-Einträge und kann bei einer Umstrukturierung gleichwertiger Netze konservativ anschlagen.

Unmittelbar vor jeder Änderung wird die Route erneut gelesen. Eine lokale HA-Store-Sicherung wird **vor** dem Schreiben gespeichert. Anschließend werden Zielnetze und alle unveränderten Richtlinienfelder erneut geprüft. Bei unklarer Antwort, fehlgeschlagener Verifikation oder unterbrochenem Schreiblauf bleibt eine persistente Schreibsperre erhalten. Nach manueller Prüfung kann die Automatik aus- und wieder eingeschaltet werden.

Mehrere Routeränderungen sind **keine atomare Transaktion**. Scheitert eine spätere Route, können vorherige bereits geändert sein; weitere automatische Schreibzugriffe bleiben gesperrt. Die letzten 20 einzelnen Routensicherungen liegen lokal in HA. Wiederherstellung erfolgt einzeln in umgekehrter Reihenfolge und verweigert das Überschreiben späterer Änderungen. Deinstallation entfernt Routerrouten nicht.

Diagnostik enthält Dienstnamen und Zähler, keine Zugangsdaten, WAN-IDs, Quellgeräte oder Ziel-IP-Listen. Sicherungen enthalten die lokale Routerkonfiguration und sollten wie andere HA-Konfigurationsdaten geschützt werden.

## Netzwerküberwachung

Optional können WAN-Leitungen, Gateway-Verkehr, ausgewählte Clients und WLAN-Verläufe in Home Assistant beobachtet werden. In den Integrationsoptionen **WAN überwachen** aktivieren, Gateway auswählen und die beiden Leitungen beispielsweise **DSL** und **Starlink** nennen.
Pro Leitung stehen Download, Upload, Latenz, Verfügbarkeit im Messzeitraum
und — sofern der Router ausdrücklich ein `up`-Feld liefert — der Linkzustand
zur Verfügung. Ein fehlender Linkzustand bleibt unverfügbar.

Das lokale Prüfintervall ist unabhängig von den öffentlichen Zielnetzquellen.
Eine gestörte Datenquelle blockiert daher nicht die WAN-Messung. WAN-Messwerte
beweisen keine IPv4-/IPv6-Erreichbarkeit und lösen keine Umschaltung aus.
Aktive IPv6-Policy-Steuerung und weitere Host-Beobachter sind getrennte, ausdrücklich einzurichtende Funktionen mit eigenen eingeschränkten SSH-Schlüsseln und gepinnten Host-Identitäten. Die Beobachter können keine Routen verändern. Für schreibende Funktionen gibt es einen lokalen Prüf- und Sperrzustand. Aufbau und Grenzen beschreiben [Netzwerkarchitektur](docs/NETWORK_ARCHITECTURE.md), [Gateway-Beobachter](docs/GATEWAY_OBSERVER.md) und [IPv6-Aktor](docs/IPV6_ACTOR.md).

## Entwicklung

Die HA-Einrichtung und die WLAN-Historienkarte sind auf Englisch und Deutsch
übersetzt. Weitere
Sprachen können als BCP-47-Dateien unter
[`translations/`](custom_components/service_route_sync/translations/) ergänzt
werden; Schlüssel und Platzhalter müssen mit `en.json` übereinstimmen. Für die
WLAN-Karte sind zusätzlich die Texte in `frontend/wifi-history-card.js` zu ergänzen.

```sh
python -m venv .venv
.venv/bin/pip install homeassistant==2026.10.0 ruff==0.17.0
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_gateway -v
.venv/bin/python -m unittest discover -s tests_ha -t . -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

`core.py` enthält routerunabhängige Planung und Schreibprüfungen, `providers.py` die Datenquellen und `backends/unifi.py` den Adapter. Der Adapter verwendet eine eigene `aiohttp`-Sitzung mit ausdrücklich konfiguriertem lokalem API-Schlüssel; Änderungen dieser Schnittstelle können Anpassungen benötigen. GitHub Actions prüft Tests, Formatierung, Python-Kompilierung, Hassfest und HACS.

## Quellen

- [Home Assistant UniFi Network](https://www.home-assistant.io/integrations/unifi/)
- [UniFi Policy-Based Routing](https://help.ui.com/hc/en-us/articles/12566175125783-UniFi-Gateway-Policy-Based-Routing)
- [RIPEstat announced prefixes](https://stat.ripe.net/docs/data-api/api-endpoints/announced-prefixes)
- [Microsoft 365 Endpoint Web Service](https://learn.microsoft.com/en-us/microsoft-365/enterprise/microsoft-365-ip-web-service)
- [Azure Service Tags](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)
- [AWS IP address ranges](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html)
- [HACS Custom Repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)

MIT-Lizenz. Kein offizielles Produkt von Ubiquiti, Microsoft, AWS oder SAP.

The optional [restricted gateway observer](docs/GATEWAY_OBSERVER.md) provides
separate native IPv4/IPv6 connectivity entities. It has a dedicated key, pinned
host identity and a verified read-only setup flow; it does not take over IPv6
failover or change routing rules.
