# Service Route Sync

[![Tests](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/FiraSenax/ha-service-route-sync/actions/workflows/tests.yml)

Eine Home-Assistant-Integration, die Zielnetze ausgewählter Spiele und Dienste in bestehenden UniFi-Routen aktuell hält. Läuft direkt in HA, nutzt die vorhandene UniFi-Anmeldung und braucht keinen externen Timer.

**Erste Version: Vorschau standardmäßig aktiv.** Router-Lesezugriffe, Einrichtung und Statusentitäten wurden mit Home Assistant **2026.9.4** und **aiounifi 95** live geprüft. Öffentliche Datenquellen wurden ebenfalls live geprüft. Router-Schreibvorgänge und Wiederherstellung sind automatisiert mit simuliertem Backend getestet; ein produktiver Schreibtest dieser neuen Integration steht noch aus.

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
| SAP | Eigene, dokumentierte **Zielnetze** der tatsächlich verwendeten SAP-Dienste |
| Eigene Dienste | Öffentliche IPv4-/IPv6-Zielnetze als CIDR |

Die Spieleauswahl deckt bekannte Betreiber-ASNs ab. Spiele können zusätzliche Server in fremden Clouds, CDNs oder Netzen nutzen. Es ist keine vollständige Spieleerkennung. Microsoft-Endpunkte ohne veröffentlichte IP-Adresse werden nicht abgebildet. SAP-Egress-/NAT-Adressen sind nicht automatisch die passenden Zielnetze für Clientzugriffe.

Für Microsoft 365 ist standardmäßig die Kategorie **Optimize** ausgewählt; **Allow** und **Default** können zusätzlich gewählt werden.

Azure und AWS werden bewusst nach konkreten Tags, Diensten und Regionen ausgewählt. Die gesamte Cloud zu routen kann viele fremde Anwendungen betreffen. Überschneidende Dienste, etwa M365 und Teams, benötigen eine bewusste Routenpriorität und Gerätezuordnung im Router.

## Router

| Backend | Stand |
| --- | --- |
| UniFi Network | Implementiert; vorhandene offizielle HA-UniFi-Integration erforderlich |
| FRITZ!Box | Noch nicht implementiert; keine gleichwertige API-Funktion zugesagt |
| Weitere Router | Backend-Schnittstelle vorhanden, konkrete Unterstützung noch offen |

Die Integration aktualisiert ausschließlich `ip_addresses` ausdrücklich zugeordneter IP-Routen. Sie legt keine Routen an und löscht keine. WAN, Quellgeräte/VLANs, Aktivierung, Kill Switch und vorhandene IP-Bereichsanker bleiben erhalten. Eine deaktivierte Routerroute bleibt deaktiviert.

## Installation

Home Assistant **2026.9 oder neuer** und eine bereits eingerichtete, geladene **UniFi Network**-Integration werden benötigt. Der UniFi-Benutzer braucht Zugriff auf Traffic Routes; für automatische Änderungen entsprechende Schreibrechte.

1. HACS öffnen → Menü → **Benutzerdefinierte Repositories**.
2. `https://github.com/FiraSenax/ha-service-route-sync` hinzufügen, Typ **Integration**.
3. **Service Route Sync** herunterladen und HA neu starten.
4. Einstellungen → Geräte & Dienste → Integration hinzufügen → **Service Route Sync**.
5. UniFi-Anbindung, Spiele/Dienste und bei Bedarf Cloud-Filter auswählen.
6. Jedem Dienst eine eigene vorhandene IP-Route zuordnen. Diese Routen zuvor im UniFi-Gateway mit gewünschtem WAN und Geräte-/Netzbereich anlegen.

Das Repository ist als HACS-Custom-Repository installierbar; eine Aufnahme in den HACS-Standardkatalog ist nicht erfolgt.

## Bedienung

Nach der Einrichtung erscheinen:

- **Status**: `preview`, `current` oder `review_required`.
- **Destination prefixes**: Anzahl der ausgewählten Zielpräfixe.
- **Routes with changes**: Routen mit Änderungen; Attribute zeigen je Dienst Präfixzahl sowie hinzugefügte/entfallende Einträge.
- **Last applied change**: Zeitpunkt einer vollständig verifizierten Änderung.
- **Refresh destinations**: Daten und Vorschau neu prüfen; bei eingeschalteter Automatik auch Änderungen anwenden.
- **Automatic route updates**: Standardmäßig **aus**. Einschalten erlaubt Änderungen und startet eine frische Prüfung.
- **Restore last route update**: Letzte einzelne Routensicherung zurückspielen, wenn die Automatik aus ist und niemand die Route inzwischen verändert hat.

Vor dem Einschalten externe Skripte oder weitere Integrationen deaktivieren, die dieselben Routen ändern. Pro UniFi-Anbindung wird eine Service-Route-Sync-Instanz eingerichtet.

Das Prüfintervall beträgt standardmäßig 24 Stunden, einstellbar zwischen einer Stunde und einer Woche. Erfolgreich geladene Quellen werden eine Stunde zwischengespeichert; erneutes Drücken lädt in dieser Zeit dieselben Quelldaten, liest den Router aber erneut. Änderungen der Integrationsoptionen schalten die Automatik wieder aus.

## Prüfungen und Wiederherstellung

Alle ausgewählten Quellen und Routen werden vor dem ersten Schreibzugriff geprüft. Leere/fehlerhafte Quellen, falsche ASN-Inhaber, private Netze, Default-Routen, Portfilter und überschrittene Präfixlimits stoppen den Lauf. Listen werden niemals abgeschnitten.

Die Vorschau zeigt auch große Änderungen. Automatisches Anwenden ist standardmäßig blockiert, wenn mehr als 50 % der bisherigen Präfixeinträge entfallen. Die Grenze ist einstellbar; sie zählt CIDR-Einträge und kann bei einer Umstrukturierung gleichwertiger Netze konservativ anschlagen.

Unmittelbar vor jeder Änderung wird die Route erneut gelesen. Eine lokale HA-Store-Sicherung wird **vor** dem Schreiben gespeichert. Anschließend werden Zielnetze und alle unveränderten Richtlinienfelder erneut geprüft. Bei unklarer Antwort, fehlgeschlagener Verifikation oder unterbrochenem Schreiblauf bleibt eine persistente Schreibsperre erhalten. Nach manueller Prüfung kann die Automatik aus- und wieder eingeschaltet werden.

Mehrere Routeränderungen sind **keine atomare Transaktion**. Scheitert eine spätere Route, können vorherige bereits geändert sein; weitere automatische Schreibzugriffe bleiben gesperrt. Die letzten 20 einzelnen Routensicherungen liegen lokal in HA. Wiederherstellung erfolgt einzeln in umgekehrter Reihenfolge und verweigert das Überschreiben späterer Änderungen. Deinstallation entfernt Routerrouten nicht.

Diagnostik enthält Dienstnamen und Zähler, keine Zugangsdaten, WAN-IDs, Quellgeräte oder Ziel-IP-Listen. Sicherungen enthalten die lokale Routerkonfiguration und sollten wie andere HA-Konfigurationsdaten geschützt werden.

## Entwicklung

```sh
python -m venv .venv
.venv/bin/pip install aiohttp aiounifi==95 ruff==0.17.0
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

`core.py` enthält routerunabhängige Planung und Schreibprüfungen, `providers.py` die Datenquellen und `backends/unifi.py` den Adapter. Der Adapter nutzt die aktuelle `aiounifi`-API der offiziellen HA-Integration; Änderungen dieser Schnittstelle können Anpassungen benötigen. GitHub Actions prüft Tests, Formatierung und Python-Kompilierung.

## Quellen

- [Home Assistant UniFi Network](https://www.home-assistant.io/integrations/unifi/)
- [UniFi Policy-Based Routing](https://help.ui.com/hc/en-us/articles/12566175125783-UniFi-Gateway-Policy-Based-Routing)
- [RIPEstat announced prefixes](https://stat.ripe.net/docs/data-api/api-endpoints/announced-prefixes)
- [Microsoft 365 Endpoint Web Service](https://learn.microsoft.com/en-us/microsoft-365/enterprise/microsoft-365-ip-web-service)
- [Azure Service Tags](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)
- [AWS IP address ranges](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html)
- [HACS Custom Repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)

MIT-Lizenz. Kein offizielles Produkt von Ubiquiti, Microsoft, AWS oder SAP.
