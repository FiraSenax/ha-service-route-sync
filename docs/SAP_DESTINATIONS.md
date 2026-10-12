# SAP-Zielnetze

Service Route Sync behandelt SAP derzeit als **manuell gepflegte Ziel-CIDRs**. Die Integration leitet aus dem Wort „SAP“ keine vollständige, sichere IP-Liste ab. SAP Cloud ALM, BTP Cloud Foundry, S/4HANA und weitere SAP-Dienste haben unterschiedliche Endpunkte.

Für SAP Cloud ALM veröffentlicht SAP [regionale IP-Adressbereiche](https://help.sap.com/docs/cloud-alm/setup-administration/region-specific-ip-address-ranges?locale=en-US). Die Tabelle unterscheidet:

- **Inbound IP Addresses (requests to SAP Cloud ALM):** mögliche Zieladressen für Anfragen von lokalen Clients an Cloud ALM.
- **Outbound IP Addresses (requests from SAP Cloud ALM):** Quelladressen für Anfragen, die Cloud ALM selbst startet. Diese Liste ist keine Zielnetzliste für Clientverkehr.

SAP weist zusätzlich auf die IP-Bereiche der **SAP BTP Cloud Foundry Applications** hin. Die passende Tenant-Region muss vor der Übernahme aus SAP for Me oder der BTP-Tenantkonfiguration geprüft werden. Frankfurt `cf-eu10` und Frankfurt EU Access `cf-eu11` sind unterschiedliche Regionen.

Die [aktuelle SAP-Liste der Cloud-ALM-Rechenzentren](https://help.sap.com/docs/cloud-alm/setup-administration/supported-data-centers) enthält diese Regionen; die manuelle `sap_prefixes`-Eingabe unterstützt jede davon und ist nicht auf Frankfurt beschränkt:

| Region | SAP BTP-ID |
| --- | --- |
| Sydney | `cf-ap10` |
| Montréal | `cf-ca10` |
| China North 3 | `cf-cn20` |
| Frankfurt | `cf-eu10` |
| Frankfurt EU Access | `cf-eu11` |
| Amsterdam | `cf-eu20` |
| Tokio | `cf-jp10` |
| Northern Virginia | `cf-us10` |
| Washington | `cf-us20` |

Nur die konkret benötigten Dienste und Regionen in `sap_prefixes` übernehmen; einzelne Adressen als `/32` notieren. Die Integration prüft die CIDR-Syntax, kann aber aus einer nackten IP-Adresse die SAP-Region und die Richtung des Verkehrs nicht beweisen. Vor dem Einschalten der automatischen Routenpflege die Vorschau und die Routerroute prüfen.

Die SAP-Hilfeseite ist keine stabile maschinenlesbare Netzliste für diesen Provider. HTML-Scraping oder eine Vermischung von Inbound- und Outbound-Spalten würde bei Änderungen stillschweigend falsche Routen erzeugen. Eine automatische SAP-Quelle sollte erst hinzukommen, wenn SAP eine dokumentierte, versionierte Schnittstelle für exakt die benötigten **Ziel**netze anbietet.
