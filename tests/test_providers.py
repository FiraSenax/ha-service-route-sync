import unittest

import bootstrap  # noqa: F401
from service_route_sync.core import SyncError
from service_route_sync.providers import FeedClient, select_prefixes


class ProviderTests(unittest.TestCase):
    def test_teams_includes_common_but_not_exchange(self):
        feed = [
            {"serviceArea": "Common", "category": "Optimize", "ips": ["8.8.8.0/24"]},
            {"serviceArea": "Skype", "category": "Optimize", "ips": ["9.9.9.0/24"]},
            {"serviceArea": "Exchange", "category": "Optimize", "ips": ["1.1.1.0/24"]},
        ]
        self.assertEqual(select_prefixes("teams", feed, {}), ["8.8.8.0/24", "9.9.9.0/24"])

    def test_azure_requires_exact_tags_and_all_requested_tags_exist(self):
        feed = {
            "values": [
                {"name": "Storage.WestEurope", "properties": {"addressPrefixes": ["8.8.8.0/24"]}},
                {"name": "AzureCloud", "properties": {"addressPrefixes": ["1.1.1.0/24"]}},
            ]
        }
        self.assertEqual(
            select_prefixes("azure", feed, {"azure_tags": ["Storage.WestEurope"]}), ["8.8.8.0/24"]
        )
        with self.assertRaises(SyncError):
            select_prefixes("azure", feed, {"azure_tags": ["Storage.NotARegion"]})

    def test_aws_requires_matching_service_and_region(self):
        feed = {
            "prefixes": [
                {"region": "eu-central-1", "service": "S3", "ip_prefix": "8.8.8.0/24"},
                {"region": "us-east-1", "service": "S3", "ip_prefix": "1.1.1.0/24"},
                {"region": "eu-central-1", "service": "EC2", "ip_prefix": "9.9.9.0/24"},
            ]
        }
        self.assertEqual(
            select_prefixes("aws", feed, {"aws_regions": ["eu-central-1"], "aws_services": ["S3"]}),
            ["8.8.8.0/24"],
        )


class SourceSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_as_owner_stops_before_prefix_fetch(self):
        client = FeedClient(None, {})
        requests = []

        async def fetch(url, **kwargs):
            requests.append(url)
            return {"data": {"holder": "OTHER COMPANY"}}

        client._fetch = fetch
        with self.assertRaises(SyncError):
            await client.prefixes("blizzard")
        self.assertEqual(len(requests), 1)

    async def test_empty_aws_selection_cannot_replace_existing_destinations(self):
        client = FeedClient(None, {"aws_regions": ["missing"], "aws_services": ["S3"]})

        async def fetch(url, **kwargs):
            return {"prefixes": []}

        client._fetch = fetch
        with self.assertRaises(SyncError):
            await client.prefixes("aws")

    async def test_sources_fail_as_one_batch(self):
        client = FeedClient(
            None,
            {"services": ["custom", "sap"], "custom_prefixes": ["8.8.8.0/24"], "sap_prefixes": []},
        )
        with self.assertRaises(SyncError):
            await client.all_prefixes()
