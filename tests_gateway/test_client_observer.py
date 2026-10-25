import sys
from pathlib import Path
import unittest
import asyncio
import contextlib
import io
import json
import os
import tempfile
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
from client_observer import (
    validate_targets,
    parse_ping,
    parse_connections,
    neighbor_matches,
    sample,
    main,
    load_targets,
    resolve_targets,
    select_targets,
)
from observer import ObservationError


MARKS = {0x21940000: "primary", 0x21960000: "secondary", 0x21660000: "vpn2", 0x20040000: "vpn1"}


class ConfiguredObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_collection_uses_configured_marks_without_personal_defaults(self):
        from client_observer import observe_clients

        with (
            patch(
                "client_observer.load_route_marks",
                return_value={131072: "primary", 262144: "secondary"},
            ),
            patch("client_observer.check_identity", AsyncMock(return_value=False)),
        ):
            rows = await observe_clients(
                {"office": {"address": "192.168.1.10", "macs": ["02:00:00:00:00:01"]}}
            )
        self.assertEqual(set(rows), {"office"})
        self.assertFalse(rows["office"]["identity_verified"])

    def test_marks_are_validated_and_optional_vpns_are_not_required(self):
        from client_observer import load_route_marks

        with patch(
            "client_observer.read_private_config",
            return_value={"primary": 131072, "secondary": 262144},
        ):
            self.assertEqual(load_route_marks(), {131072: "primary", 262144: "secondary"})
        for values in (
            {"primary": 1, "secondary": 2},
            {"primary": 131072, "secondary": 131072},
            {"primary": "131072", "secondary": 262144},
        ):
            with (
                patch("client_observer.read_private_config", return_value=values),
                self.assertRaises(ObservationError),
            ):
                load_route_marks()


class ClientObserverTests(unittest.TestCase):
    def test_targets_cannot_select_public_or_special_addresses(self):
        for address in [
            "1.1.1.1",
            "127.0.0.1",
            "0.0.0.0",
            "169.254.1.1",
            "224.0.0.1",
            "::1",
            "192.168.1.2;id",
        ]:
            with self.assertRaises(ObservationError):
                validate_targets({"pc": {"address": address, "macs": ["02:00:00:00:00:01"]}})
        self.assertEqual(
            validate_targets({"pc": {"address": "172.20.1.10", "macs": ["02:00:00:00:00:01"]}})[
                "pc"
            ]["address"],
            "172.20.1.10",
        )

    def test_duplicate_targets_rejected(self):
        with self.assertRaises(ObservationError):
            validate_targets(
                {
                    key: {"address": "172.20.1.10", "macs": ["02:00:00:00:00:01"]}
                    for key in ("a", "b")
                }
            )

    def test_ping_failure_unknown_and_true_loss_separate(self):
        self.assertIsNone(parse_ping(2, "", ""))
        self.assertIsNone(parse_ping(1, "100% packet loss", "Network unreachable"))
        row = parse_ping(1, "100% packet loss", "")
        self.assertEqual(row, {"loss": 100, "average_ms": None, "maximum_ms": None})
        row = parse_ping(0, "0% packet loss\nrtt min/avg/max/mdev = 0.1/0.2/0.3/0.0 ms", "")
        self.assertEqual(row["average_ms"], 0.2)

    def test_real_routing_marks_and_failed_read(self):
        src = "172.20.1.10"
        lines = "\n".join(
            "tcp src=" + src + " dst=1.1.1.1 src=1.1.1.1 dst=" + src + " mark=" + str(mark)
            for mark in [0x21940000, 0x21960000, 0x21660000, 0x20040000, 0]
        )
        self.assertEqual(
            parse_connections(0, lines, src, MARKS),
            {"primary": 1, "secondary": 1, "vpn1": 1, "vpn2": 1, "other": 1},
        )
        self.assertIsNone(parse_connections(1, "", src, MARKS))
        self.assertEqual(parse_connections(0, "", src, MARKS)["primary"], 0)
        with self.assertRaises(ObservationError):
            parse_connections(0, "tcp src=172.20.1.11 dst=1.1.1.1 mark=0", src, MARKS)


class ResolutionTests(unittest.TestCase):
    def test_dynamic_profile_resolves_only_exact_unique_private_neighbor(self):
        target = {"address": None, "macs": ["02:00:00:00:00:01"]}
        targets = validate_targets({"pc": target})
        row = {"dst": "172.20.1.10", "lladdr": target["macs"][0], "state": ["REACHABLE"]}
        self.assertEqual(resolve_targets(targets, [row])["pc"]["address"], row["dst"])
        for neighbors in [
            None,
            [],
            [row, row],
            [{**row, "lladdr": "02:00:00:00:00:02"}],
            [{**row, "dst": "1.1.1.1"}],
        ]:
            self.assertIsNone(resolve_targets(targets, neighbors)["pc"]["address"])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.targets = validate_targets(
            {"pc": {"address": None, "macs": ["02:00:00:00:00:01", "02:00:00:00:00:02"]}}
        )

    def test_selected_alias_avoids_other_nic_neighbor_ambiguity(self):
        selected = select_targets(self.targets, {"schema": 1, "addresses": {"pc": "172.20.1.12"}})
        self.assertEqual(selected["pc"]["address"], "172.20.1.12")
        self.assertEqual(selected["pc"]["macs"], self.targets["pc"]["macs"])
        self.assertTrue(
            neighbor_matches(
                [{"dst": "172.20.1.12", "lladdr": "02:00:00:00:00:02", "state": ["REACHABLE"]}],
                selected["pc"],
            )
        )
        self.assertFalse(
            neighbor_matches(
                [{"dst": "172.20.1.12", "lladdr": "02:00:00:00:00:03", "state": ["REACHABLE"]}],
                selected["pc"],
            )
        )

    def test_public_special_and_unknown_profile_selections_rejected(self):
        for address in [
            "1.1.1.1",
            "192.0.2.1",
            "198.18.0.1",
            "0.0.0.1",
            "127.0.0.1",
            "172.20.1.1;id",
        ]:
            with self.assertRaises(ObservationError):
                select_targets(self.targets, {"schema": 1, "addresses": {"pc": address}})
        with self.assertRaises(ObservationError):
            select_targets(self.targets, {"schema": 1, "addresses": {"other": "172.20.1.12"}})

    def test_offline_selection_never_falls_back_to_stale_neighbor(self):
        selected = select_targets(self.targets, {"schema": 1, "addresses": {"pc": None}})
        self.assertIsNone(selected["pc"]["address"])

    def test_fixed_target_cannot_be_widened(self):
        self.targets["pc"]["address"] = "172.20.1.11"
        selected = select_targets(self.targets, {"schema": 1, "addresses": {"pc": "172.20.1.12"}})
        self.assertIsNone(selected["pc"]["address"])


class EntrypointTests(unittest.TestCase):
    def test_arbitrary_and_write_commands_denied_before_file_access(self):
        for command in ["", "id", "service-route-sync clients v1 extra", "reboot"]:
            with (
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}),
                patch("client_observer.os.open") as opened,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(main(), 2)
            opened.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["error_code"], "command_denied")

    def test_private_config_requires_exact_schema_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clients.json"
            owner = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
            fileinfo = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
            target = {"pc": {"address": "172.20.1.10", "macs": ["02:00:00:00:00:01"]}}
            with (
                patch("pathlib.Path.lstat", return_value=owner),
                patch("client_observer.os.fstat", return_value=fileinfo),
            ):
                path.write_text(json.dumps({"schema": 1, "targets": target}))
                self.assertEqual(load_targets(path), target)
                for raw in [
                    {"schema": True, "targets": target},
                    {"schema": 1, "targets": target, "secret": "private"},
                ]:
                    path.write_text(json.dumps(raw))
                    with self.assertRaises(ObservationError):
                        load_targets(path)
                path.write_bytes(b"x" * 16385)
                with self.assertRaises(ObservationError):
                    load_targets(path)

    def test_symlink_config_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clients.json"
            source = Path(directory) / "target"
            source.write_text("private")
            path.symlink_to(source)
            owner = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
            with patch("pathlib.Path.lstat", return_value=owner):
                with self.assertRaises(OSError):
                    load_targets(path)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = {"address": "172.20.1.10", "macs": ("02:00:00:00:00:01",)}

    async def test_missing_identity_prevents_probe_commands(self):
        runner = AsyncMock()
        with (
            patch("client_observer.check_identity", AsyncMock(return_value=False)),
            patch("client_observer.run", runner),
        ):
            _, row = await sample("pc", self.target, asyncio.Semaphore(1), MARKS)
        runner.assert_not_awaited()
        self.assertFalse(row["identity_verified"])
        self.assertIsNone(row["probe"])

    async def test_identity_change_discards_results(self):
        runner = AsyncMock(side_effect=[(1, b"100% packet loss", b""), (0, b"", b"")])
        with (
            patch("client_observer.check_identity", AsyncMock(side_effect=[True, False])),
            patch("client_observer.run", runner),
        ):
            _, row = await sample("pc", self.target, asyncio.Semaphore(1), MARKS)
        self.assertIsNone(row["probe_at_ms"])
        self.assertIsNone(row["connections"])
        self.assertFalse(row["identity_verified"])

    async def test_neighbor_requires_exact_unambiguous_mac_and_ip(self):
        row = {
            "dst": self.target["address"],
            "lladdr": self.target["macs"][0],
            "state": ["REACHABLE"],
        }
        self.assertTrue(neighbor_matches([row], self.target))
        for rows in [
            [],
            [row, row],
            [{**row, "state": ["FAILED"]}],
            [{**row, "lladdr": "02:00:00:00:00:02"}],
            [{**row, "dst": "172.20.1.11"}],
        ]:
            self.assertFalse(neighbor_matches(rows, self.target))


if __name__ == "__main__":
    unittest.main()
