from types import SimpleNamespace
import unittest

import bootstrap  # noqa: F401
from service_route_sync.backends.http_json import read_json
from service_route_sync.routing.core import SyncError


class HttpJsonTests(unittest.IsolatedAsyncioTestCase):
    def response(self, pieces):
        async def chunks(size):
            for piece in pieces:
                yield piece

        return SimpleNamespace(content=SimpleNamespace(iter_chunked=chunks))

    async def test_compressed_bomb_and_truncated_stream_are_bounded(self):
        import gzip

        response = self.response([gzip.compress(b"x" * 1_000_000)])
        response.headers = {"Content-Encoding": "gzip"}
        with self.assertRaisesRegex(SyncError, "size limit"):
            await read_json(response, maximum=1000)
        response = self.response([gzip.compress(b'{"a":1}')[:-1]])
        response.headers = {"Content-Encoding": "gzip"}
        with self.assertRaisesRegex(SyncError, "Incomplete"):
            await read_json(response)

    async def test_compressed_json_at_limit_and_unknown_encodings(self):
        import gzip

        response = self.response([gzip.compress(b'{"a":1}')])
        response.headers = {"Content-Encoding": "gzip"}
        self.assertEqual(await read_json(response, maximum=7), {"a": 1})
        response.headers = {"Content-Encoding": "unsupported"}
        with self.assertRaises(SyncError):
            await read_json(response)

    async def test_missing_or_false_length_cannot_bypass_decoded_body_limit(self):
        response = self.response([b'{"x":"', b"x" * 100, b'"}'])
        response.content_length = 1
        with self.assertRaisesRegex(SyncError, "size limit"):
            await read_json(response, maximum=20)

    async def test_accepts_chunked_complete_json_at_bound(self):
        self.assertEqual(await read_json(self.response([b'{"a":', b"1}"]), maximum=7), {"a": 1})

    async def test_malformed_duplicate_nonfinite_and_deep_payloads_are_sanitized(self):
        for raw in [
            b'{"private":',
            b'{"a":1,"a":2}',
            b'{"a":NaN}',
            b'{"a":1e999}',
            '{"a":1}'.encode("utf-16"),
            b"\xff",
            b"[" * 1500 + b"0" + b"]" * 1500,
        ]:
            with self.subTest(raw=raw[:8]), self.assertRaises(SyncError) as error:
                await read_json(self.response([raw]))
            self.assertNotIn("private", str(error.exception))
