# Changelog

## 0.2.2

- Wi-Fi history card now follows the Home Assistant language for English and German, including errors, controls, and access messages.
- Unknown languages use English card text; changing HA language rerenders the card without refetching private history.

## 0.2.1

- English README for international HACS readers, with the German version kept separately.
- Document the existing English and German HA translations and how to contribute further languages.

## 0.2.0

- Optionale WAN-, Gateway-, Client- und WLAN-Beobachtung direkt in Home Assistant.
- Getrennte, ausdrücklich einzurichtende IPv6-Policy-Steuerung mit geprüfter Rücklesung und dauerhafter Sperre bei unklarem Zustand.
- Begrenzter WLAN-Verlauf mit eigener Karte sowie optionale Frigate- und Windows-Beobachter.
- Zwei private DNS-Resolver für die zusätzliche Netzwerkdiagnose in den HA-Optionen konfigurierbar.
- Automatisierte Prüfungen mit HA 2026.10, HACS, Hassfest, Chromium und Frontend-Tests.
- SAP-Cloud-ALM-Regionen und die Grenzen der manuell gepflegten Ziel-CIDRs dokumentiert.

## 0.1.0

- Erste HACS-Custom-Integration mit bestehender HA-UniFi-Anbindung.
- Auswählbare Spiele-ASNs, Microsoft 365, Azure Service Tags, AWS-Filter und eigene SAP-/Dienstpräfixe.
- Vorschau, explizite Schreibfreigabe, lokale Sicherungen, geschützte Wiederherstellung und persistente Sperre bei unklaren Schreibvorgängen.
- Deutsche und englische Einrichtung; Statussensoren und Bedienelemente.
