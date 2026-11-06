# WLAN-Historie

Die optionale WLAN-Historie erfasst **alle** aktuell beobachteten WLAN-Clients.
Sie ist unabhängig von ausgewählten Clientprofilen. Aktivierung erfolgt über die
Option „WLAN-Historie aller Clients alle 30 Minuten speichern (lokal)“.

## Speicherung und Fehlerverhalten

UniFi wird über die eigene konfigurierte API-Key-Verbindung dieser Integration
alle 30 Sekunden gelesen. Pro UTC-Halbstundenfenster wird die erste vollständige
Beobachtung atomar gespeichert. Wiederholungen und HA-Neustarts erzeugen keine zweite
Probe im selben Fenster. Ein erfolgreicher leerer Scan ist von einem
fehlgeschlagenen Abruf unterscheidbar. Unbekannte Messwerte bleiben `null`.

Die SQLite-Datei liegt in einem privaten Unterverzeichnis von `.storage`,
mit Verzeichnismodus 0700 und Dateimodus 0600. Die Erweiterung verwaltet sie
selbst; keine manuelle Bearbeitung von HA-Registry-Dateien ist erforderlich.
WAL und FULL-Synchronisierung sichern Transaktionen. Ein Schreibfehler macht
die aktuelle Abfrage erfolglos. Statussensoren werden bei Fehlern, zukünftigen
Zeitstempeln oder mehr als 75 Sekunden alten Beobachtungen nicht verfügbar.
Private Clientdetails werden nicht als Sensorattribute im Recorder abgelegt.
Es gibt derzeit keine automatische Löschung der Historie.

## Administratorzugriff

Der WebSocket-Befehl `service_route_sync/wifi_history` erfordert einen
angemeldeten Administrator. Er akzeptiert `entry_id`, `source` (`legacy` oder
`observations`), `after`, optional `ceiling` und `limit` (1–100).
Die Antwort enthält `columns`, `rows`, `after`, `ceiling` und `more`.
Für Folgeseiten werden `after` und `ceiling` aus der vorherigen Antwort
übernommen. Das Ceiling friert den Datenbestand für diesen Abruf ein.
Neue Messungen erscheinen beim nächsten vollständigen Abruf.

Die Kartenressource wird als JavaScript-Modul unter
`/service_route_sync/frontend/wifi-history-card.js` ausgeliefert.
Nach Registrierung als Lovelace-Ressource kann folgende Karte verwendet werden:

```yaml
type: custom:service-route-sync-wifi-history
entry_id: <Service-Route-Sync-Konfigurationseintrag>
```

Die Karte zeigt native Messungen und importierte Bestandsdaten getrennt,
jeweils in Seiten von bis zu 50 Datensätzen. Sie fordert für Nichtadministratoren
keine Historie an; der Server erzwingt diese Einschränkung zusätzlich.
Alle gespeicherten Texte werden mit `textContent` dargestellt.

## Geprüfter Bestandsimport

`service_route_sync.import_wifi_history` verlangt `entry_id` und eine
kleingeschriebene SHA-256-Prüfsumme (`sha256`). Nur Administratoren mit
authentifiziertem Benutzerkontext dürfen den Dienst ausführen.
Er akzeptiert keine frei angegebenen Dateipfade.

Ein Administrator stellt die Datei im privaten Journalverzeichnis
`.storage/service_route_sync_wifi` als
`<SHA256-des-entry_id>.<SHA256-des-CSV-Inhalts>.csv` bereit. Quelle und Verzeichnis
müssen dem HA-Prozess gehören und privat sein. Symlinks und mehrfach verlinkte
Quelldateien werden abgewiesen. Die CSV muss UTF-8-kodiert sein und exakt
folgenden Header in dieser Reihenfolge enthalten:

```csv
ts,mac,name,essid,ap_mac,radio,channel,width,signal,noise,tx_rate,rx_rate,nss,retry_pct,tx_retries,wifi_tx_attempts,satisfaction
```

Dateien sind auf 256 MiB, eine Million Datenzeilen und 8192 Zeichen je Zelle
begrenzt. Der Import prüft den Hash und verwirft Änderungen am Quellinode
während des Imports. Ungültige Daten rollen die gesamte Transaktion zurück.
Bestandszellen bleiben unverändert; derselbe Dateiinhalt wird nur einmal importiert.

## Prüfung und Betrieb

Die automatisierten Tests prüfen Speicherung, wiederholten Import, Berechtigungen
und die Kartendarstellung mit synthetischen Daten in Chromium. Die Testumgebungen
und Grenzen der Live-Prüfung sind unter [Validierung](VALIDATION.md) dokumentiert.

Ein CSV-Import ist optional; neue Installationen beginnen mit nativen Messungen.
Bei einer Übernahme vorhandener Daten die CSV und HA-Konfiguration sichern,
Zeilen und Inhalte nach dem Import vergleichen und eine neue Messung nach einem
HA-Neustart prüfen. Den bisherigen Sammler erst nach erfolgreicher Prüfung
abschalten. Datenbankgröße und freien Speicher beobachten, da keine automatische
Aufbewahrungsgrenze implementiert ist.
