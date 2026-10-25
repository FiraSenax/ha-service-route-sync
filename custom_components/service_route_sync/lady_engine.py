"""Persistable incident logic. No network calls; deterministic fixture tests."""

import datetime
from homeassistant.util import dt as dt_util
import copy

from .network import finite_number


def counter_delta(current, previous):
    """Never invent zero for a missing counter or count a reboot as growth."""
    current = finite_number(current)
    previous = finite_number(previous)
    if current is None or previous is None or current < previous:
        return None
    return current - previous


def pc_monitoring_status(row, state, now):
    """Explain why PC-specific notifications are active or suspended."""
    if state.get("expected_power_off"):
        return "powered_off"
    port = row.get("port")
    if row.get("api_ok") is not True or not isinstance(port, dict):
        return "source_unavailable"
    if port.get("up") is False:
        return "port_disconnected"
    if port.get("up") is not True:
        return "source_unavailable"
    lan = row.get("probes", {}).get("lan", {})
    agent = row.get("windows", {}).get("agent")
    heartbeat = agent.get("last_keepalive") if isinstance(agent, dict) else None
    fresh_heartbeat = type(heartbeat) in (int, float) and 0 <= now - heartbeat <= 90
    if lan.get("loss") == 0 or fresh_heartbeat:
        return "active"
    if row.get("probe_ok") is not True and not fresh_heartbeat:
        return "source_unavailable"
    return "no_pc_evidence"


class LadyIncidentEngine:
    def __init__(self, state=None, expected_speed=1000, maintenance_hour=-1, maintenance_minutes=2):
        self.s = copy.deepcopy(state) if state is not None else {}
        self.s.setdefault("incidents", {})
        self.events = []
        self.expected_speed = expected_speed
        self.maintenance_hour = maintenance_hour
        self.maintenance_minutes = maintenance_minutes

    def emit(self, key, message, now, recovery=False):
        self.events.append({"key": key, "message": message, "time": now, "recovery": recovery})

    def check(self, key, bad, message, now, delay=10):
        x = self.s["incidents"].setdefault(key, {"since": None, "active": False, "good": None})
        if bad is None:
            # Unknown evidence cannot recover an incident or qualify a timer.
            x["since"] = None
            x["good"] = None
            return
        if type(bad) is not bool:
            raise ValueError("Incident evidence must be boolean or unknown")
        if bad:
            x["good"] = None
            if x["since"] is None:
                x["since"] = now
            if not x["active"] and now - x["since"] >= delay and now - x.get("last_sent", 0) >= 300:
                x["active"] = True
                x["last_sent"] = now
                self.emit(key, message, now)
        else:
            x["since"] = None
            if x["active"]:
                if x["good"] is None:
                    x["good"] = now
                if now - x["good"] >= 30:
                    x["active"] = False
                    self.emit(key, "Wieder unauffällig: " + key, now, True)

    def pulse(self, key, message, now, cooldown=300):
        last = self.s.setdefault("last_pulse", {}).get(key, 0)
        if now - last >= cooldown:
            self.s["last_pulse"][key] = now
            self.emit(key, message, now)

    def windows(self, events, now):
        if not isinstance(events, list) or len(events) > 1000:
            raise ValueError("Invalid Windows event batch")
        for e in events:
            if not isinstance(e, dict):
                continue
            stamp, provider = e.get("time"), e.get("provider")
            eid, msg = e.get("id"), e.get("message", "")
            if (
                not isinstance(stamp, str)
                or len(stamp) > 64
                or not isinstance(provider, str)
                or not 1 <= len(provider) <= 200
                or not isinstance(msg, str)
                or len(msg) > 8192
            ):
                continue
            if type(eid) is int and 0 <= eid <= 65535:
                eid = str(eid)
            if not isinstance(eid, str) or not eid.isascii() or not eid.isdecimal() or len(eid) > 5:
                continue
            try:
                observed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if observed.tzinfo is None or observed.utcoffset() is None:
                    continue
                t = observed.timestamp()
            except (ValueError, OverflowError, OSError):
                continue
            if not 0 <= now - t <= 300:
                continue
            p = provider.lower()
            msg = msg.strip('"')
            if (
                ("kernel-power" in p and eid in ["42", "109"])
                or ("user32" in p and eid == "1074")
                or ("kernel-general" in p and eid == "13")
            ):
                self.s["power_off"] = max(t, self.s.get("power_off", 0))
                continue
            if (
                ("power-troubleshooter" in p and eid == "1")
                or ("kernel-general" in p and eid == "12")
                or ("eventlog" in p and eid == "6005")
            ):
                self.s["power_on"] = max(t, self.s.get("power_on", 0))
                continue
            if "dns-client" in p and eid == "1014":
                # Reverse lookup failures retained in raw evidence; no gaming-causality claim.
                if "in-addr.arpa" not in msg and "ip6.arpa" not in msg:
                    self.pulse(
                        "Windows DNS", "Windows meldet DNS-Zeitüberschreitung: " + msg[:400], now
                    )
            elif any(
                x in p for x in ["rt640", "rt25", "rt68", "realtek", "ndis", "tcpip", "dhcp"]
            ) and (
                e.get("level") in ["1", "2", "3"]
                or (any(x in p for x in ["rt640", "rt25", "rt68", "realtek"]) and eid == "27")
            ):
                if now - max(self.s.get("power_off", 0), self.s.get("power_on", 0)) > 120:
                    self.pulse(
                        "Windows Netzwerk",
                        "Windows-Netzwerkereignis " + provider + " / " + eid + ": " + msg[:400],
                        now,
                    )

    def link_flaps(self, row, now, expected_off, recent_power):
        # A disconnected port and delayed switch counters are not evidence of
        # an unexpected PC link fault.
        if not row.get("api_ok") or "port" not in row:
            return
        p = row["port"]
        count = finite_number(p.get("link_down_count"))
        uptime = finite_number(row.get("switch_uptime"))
        identity = row.get("port_identity")
        if count is None or uptime is None or not identity:
            return
        key = "LAN-Linkabbrüche gehäuft"
        previous = self.s.get("link_flap_window")
        incident = self.s["incidents"].setdefault(
            key, {"active": False, "since": None, "good": None}
        )
        reset = (
            not previous
            or previous["port"] != identity
            or count < previous["count"]
            or uptime < previous["uptime"]
            or now - previous["time"] > 120
            or expected_off
            or recent_power
            or p.get("up") is not True
            or now - self.s.get("up_since", now) < 120
            or row.get("planned_link_test", False)
        )
        if reset:
            self.s["link_flap_window"] = {
                "port": identity,
                "count": count,
                "uptime": uptime,
                "time": now,
                "deltas": [],
                "last_change": now,
            }
            # A measurement gap or switch-counter reset is not proof that a
            # previously active physical-link incident has recovered.
            if (
                expected_off
                or recent_power
                or p.get("up") is not True
                or row.get("planned_link_test", False)
            ):
                incident.update(active=False, since=None, good=None)
            else:
                incident.update(since=None, good=None)
            return
        delta = count - previous["count"]
        deltas = [x for x in previous["deltas"] if now - x[0] < 300]
        if delta > 0:
            deltas.append([now, delta])
            previous["last_change"] = now
        previous.update(count=count, uptime=uptime, time=now, deltas=deltas)
        total = sum(x[1] for x in deltas)
        if total >= 3 and not incident["active"]:
            incident.update(active=True, since=now, good=None, last_sent=now)
            self.emit(
                key,
                f"{identity}: {int(total)} neue Linkabbrüche innerhalb von 5 Minuten. LAN-Verbindung instabil; Kabelweg, Netzwerkkarte und Port prüfen. Aktuell "
                + ("verbunden" if p.get("up") else "getrennt")
                + ". Zähler können verzögert eintreffen.",
                now,
            )
        elif incident["active"] and now - previous["last_change"] >= 300 and p.get("up"):
            incident.update(active=False, since=None, good=now)
            self.emit(
                key,
                f"{identity}: seit 5 Minuten keine weiteren Linkabbrüche, LAN-Link wieder verbunden.",
                now,
                True,
            )

    def sample(self, row, now):
        self.events = []
        self.windows(row.get("windows", {}).get("events", []), now)
        for key in ["api", "probe", "windows"]:
            if key + "_ok" in row:
                self.check(
                    "Messquelle " + key,
                    not row[key + "_ok"],
                    "Überwachung gestört: "
                    + key
                    + ". Keine verlässliche Aussage zu dieser Messquelle.",
                    now,
                    120,
                )
        port = row.get("port")
        probe = row.get("probes", {})
        # Missing evidence breaks qualification/recovery timers, while leaving
        # the active diagnosis intact until fresh measurements can recover it.
        unknown = set()
        if port is None or port.get("up") is not True:
            unknown.update(
                ("LAN-Geschwindigkeit", "LAN-Paketverlust", "LAN-Latenz", "Windows-Agent")
            )
        else:
            if port.get("speed") is None:
                unknown.add("LAN-Geschwindigkeit")
            lan_evidence = probe.get("lan", {})
            if lan_evidence.get("loss") is None:
                unknown.add("LAN-Paketverlust")
            if lan_evidence.get("avg") is None:
                unknown.add("LAN-Latenz")
            if row.get("windows", {}).get("agent") is None:
                unknown.add("Windows-Agent")
        dns = row.get("dns", {})
        for key in self.s["incidents"]:
            if key.startswith("DNS ") and key[4:] not in dns:
                unknown.add(key)
        for key in unknown & self.s["incidents"].keys():
            self.check(key, None, "", now)
        old = self.s.get("port")
        identity = row.get("port_identity", self.s.get("port_identity"))
        if identity != self.s.get("port_identity"):
            old = None
            self.s["port_identity"] = identity
            self.s["up_since"] = now
            self.s.pop("down_since", None)
        label = identity or "PC-Port"
        off = self.s.get("power_off", 0)
        expected_off = off > self.s.get("power_on", 0)
        lan = probe.get("lan", {})
        if expected_off and now - off > 120 and lan.get("loss") == 0:
            self.s.setdefault("wake_replies_since", now)
            if now - self.s["wake_replies_since"] >= 15:
                self.s["power_on"] = now
                self.s["up_since"] = now
                expected_off = False
        else:
            self.s.pop("wake_replies_since", None)
        self.s["expected_power_off"] = expected_off
        agent = row.get("windows", {}).get("agent")
        heartbeat = agent.get("last_keepalive") if isinstance(agent, dict) else None
        fresh_heartbeat = type(heartbeat) in (int, float) and 0 <= now - heartbeat <= 90
        pc_active = (
            not expected_off
            and port is not None
            and port.get("up") is True
            and (lan.get("loss") == 0 or fresh_heartbeat)
        )
        if not pc_active:
            # Normal shutdown, standby and Wake-on-LAN are not Lady network
            # incidents. House WAN monitoring remains independent of this PC.
            for key, incident in self.s["incidents"].items():
                incident.update(active=False, since=None, good=None)
            self.events = []
            self.link_flaps(row, now, True, False)
            self.s["up_since"] = now
            if port is not None:
                self.s["port"] = dict(port)
                self.s["switch_uptime"] = row.get("switch_uptime", 0)
            return self.events
        self.wan_dns(row, now)
        recent_power = now - max(self.s.get("power_off", 0), self.s.get("power_on", 0)) < 120
        self.link_flaps(row, now, expected_off, recent_power)
        if port is not None:
            if port.get("up"):
                if old and old.get("up") is False:
                    self.s["up_since"] = now
                elif old and old.get("up") is None:
                    self.s["up_since"] = now
                stable = now - self.s.get("up_since", 0) > 120
                uptime = finite_number(row.get("switch_uptime"))
                previous_uptime = finite_number(self.s.get("switch_uptime"))
                if (
                    old
                    and uptime is not None
                    and previous_uptime is not None
                    and uptime >= previous_uptime
                ):
                    delta = counter_delta(port.get("link_down_count"), old.get("link_down_count"))
                    if (
                        delta is not None
                        and delta > 0
                        and old.get("up")
                        and not recent_power
                        and not expected_off
                        and stable
                        and not row.get("planned_link_test")
                        and not self.s["incidents"]
                        .get("LAN-Linkabbrüche gehäuft", {})
                        .get("active")
                    ):
                        self.pulse(
                            "LAN-Linkwechsel",
                            f"{label} meldet {delta} neue Link-Unterbrechung(en), jetzt wieder verbunden. Primär-WAN separat prüfen; Ausschalten/Standby als mögliche Ursache berücksichtigen.",
                            now,
                        )
                    for f in ["rx_errors", "tx_errors", "rx_dropped", "tx_dropped"]:
                        d = counter_delta(port.get(f), old.get(f))
                        if d is not None and d > 0:
                            self.pulse(
                                "Port " + f, f"{label}: {d} neue {f}, Gesamtzähler {port[f]}.", now
                            )
                self.check(
                    "LAN-Geschwindigkeit",
                    (not expected_off and stable and port["speed"] != self.expected_speed)
                    if port.get("speed") is not None
                    else None,
                    f"LAN-Port handelt {port.get('speed')} statt {self.expected_speed} Mbit/s aus.",
                    now,
                    30,
                )
                self.s.pop("down_since", None)
            elif port.get("up") is False:
                self.s.setdefault("down_since", now)
            else:
                self.s.pop("down_since", None)
                self.s["up_since"] = now
            self.s["port"] = dict(port)
            self.s["switch_uptime"] = row.get("switch_uptime", 0)
        if not port or not port.get("up"):
            return self.events
        lan = probe.get("lan", {})
        if not expected_off and lan.get("loss") is not None:
            self.check(
                "LAN-Paketverlust",
                lan["loss"] > 0,
                f"Paketverlust zum PC bei aktivem LAN-Link: {lan['loss']} % in kurzer ICMP-Stichprobe; kein gemessener Spielpaketverlust.",
                now,
                10,
            )
            if lan.get("avg") is not None:
                self.check(
                    "LAN-Latenz",
                    lan["avg"] > 10,
                    f"LAN-Ping zum PC erhöht: {lan['avg']} ms.",
                    now,
                    15,
                )
        agent = row.get("windows", {}).get("agent")
        if agent and not expected_off:
            self.check(
                "Windows-Agent",
                now - agent.get("last_keepalive", 0) > 180,
                "PC am LAN sichtbar, aber Windows-Ereignissammlung seit über drei Minuten ohne Lebenszeichen.",
                now,
                30,
            )
        r = probe.get("routing")
        if r and r.get("rc") == 0:
            n = sum(v for k, v in r.get("counts", {}).items() if k != "primary")
            if n:
                self.pulse(
                    "Routing-Abweichung",
                    f"{n} aktuelle öffentliche IPv4-Verbindungen des überwachten Clients laufen über andere Markierungen als Primär-WAN: "
                    + str(r["counts"])
                    + ". Anwendung noch nicht zugeordnet.",
                    now,
                    900,
                )
        return self.events

    def wan_dns(self, row, now):
        probe = row.get("probes", {})
        local = datetime.datetime.fromtimestamp(now, dt_util.get_default_time_zone())
        maintenance = (
            local.hour == self.maintenance_hour and local.minute < self.maintenance_minutes
        )
        w = [probe.get("wan_cf", {}), probe.get("wan_google", {})]
        known_loss = all(x.get("loss") is not None for x in w)
        if not maintenance:
            self.check(
                "Primär-WAN-Testpfad",
                (
                    True
                    if any(x.get("loss") is None and x.get("rc") not in (None, 0) for x in w)
                    else False
                    if known_loss
                    else None
                ),
                "Primär-WAN-Testpfad nicht verfügbar: WAN-Interface oder Ping-Prüfung fehlgeschlagen.",
                now,
                10,
            )
        if not maintenance:
            self.check(
                "Primär-WAN-Paketverlust",
                all(x["loss"] > 0 for x in w) if known_loss else None,
                "Primär-WAN-Paketverlust zu zwei unabhängigen Testzielen. PC-LAN separat protokolliert; mögliche Internetstörung.",
                now,
                10,
            )
            self.check(
                "Primär-WAN-Latenz",
                all(x["avg"] > 80 for x in w) if all(x.get("avg") is not None for x in w) else None,
                "Primär-WAN-Latenz zu beiden Testzielen über 80 ms.",
                now,
                15,
            )
        if "dns" in row:
            for server, v in row["dns"].items():
                self.check(
                    "DNS " + server,
                    not v["ok"] if type(v.get("ok")) is bool else None,
                    "DNS-Test über " + server + " schlägt fehl (Messung vom Hausserver).",
                    now,
                    30,
                )
