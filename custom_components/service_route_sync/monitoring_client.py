"""Monitoring-specific projection of shared read-only UniFi observations."""

from .client_observation import ClientDataError, ClientObservation, canonical_mac


def incident_port(client):
    if not isinstance(client, ClientObservation):
        raise ClientDataError("Invalid Monitoring client observation")
    port = client.port
    if client.port_ambiguous or port is None:
        return {}
    switch, index = port.identity.rsplit(":", 1)
    if (
        canonical_mac(switch) != switch
        or not index.isascii()
        or not index.isdigit()
        or not 1 <= int(index) <= 65535
    ):
        raise ClientDataError("Invalid Monitoring port identity")
    # Legacy incidents use the display identity; preserve it during migration.
    # The tracked MAC/index remains separate and is the authoritative location.
    return {
        "port_identity": port.name,
        "tracked_port": {"switch": switch, "port": int(index)},
        "switch_uptime": port.switch_uptime_s,
        "port": {
            "up": port.up,
            "speed": port.speed_mbit_s,
            "link_down_count": port.link_down_count,
            "rx_errors": port.rx_errors,
            "tx_errors": port.tx_errors,
            "rx_dropped": port.rx_dropped,
            "tx_dropped": port.tx_dropped,
            "rx_bytes": port.rx_bytes,
            "tx_bytes": port.tx_bytes,
        },
    }


class MonitoringClientReader:
    """Read one explicitly configured Monitoring profile without widening Gaming export."""

    def __init__(self, coordinator, profile_id):
        if profile_id not in coordinator.profiles:
            raise ClientDataError("Monitoring profile is not explicitly configured")
        self.coordinator = coordinator
        self.profile_id = profile_id

    async def __call__(self):
        from .monitoring_coordinator import TimedObservation

        if not self.coordinator.last_update_success or self.coordinator.data is None:
            raise ClientDataError("Monitoring client source unavailable")
        snapshot = self.coordinator.data
        client = snapshot.clients.get(self.profile_id)
        if client is None:
            raise ClientDataError("Monitoring client profile missing from snapshot")
        return TimedObservation(incident_port(client), snapshot.observed_at.timestamp())
