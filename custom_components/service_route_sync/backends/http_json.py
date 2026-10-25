"""Bounded JSON decoding for authenticated controller responses."""

import json
import asyncio
import math
import zlib

from ..routing.core import SyncError

MAX_CONTROLLER_BYTES = 4 * 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def _check_depth(body, maximum=64):
    depth = 0
    quoted = escaped = False
    for byte in body:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > maximum:
                raise ValueError("Excessive JSON nesting")
        elif byte in (93, 125):
            depth -= 1


async def read_body(response, *, maximum):
    """Bound allocation before decompression, including hostile compressed bodies.

    Callers must disable aiohttp automatic decompression and request only the
    supported encodings. Content-Length is not a resource limit.
    """
    encoding = getattr(response, "headers", {}).get("Content-Encoding", "identity").lower()
    if encoding not in ("identity", "gzip", "deflate"):
        raise SyncError("Unsupported response content encoding")
    decoder = (
        zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding != "identity" else None
    )
    body = bytearray()
    encoded_bytes = 0
    try:
        async for chunk in response.content.iter_chunked(65536):
            encoded_bytes += len(chunk)
            if encoded_bytes > maximum + 65536:
                raise SyncError("Response exceeds size limit")
            decoded = decoder.decompress(chunk, maximum - len(body) + 1) if decoder else chunk
            if len(body) + len(decoded) > maximum:
                raise SyncError("Response exceeds size limit")
            body.extend(decoded)
            if decoder and decoder.unused_data:
                raise SyncError("Unexpected trailing compressed content")
        if decoder and not decoder.eof:
            raise SyncError("Incomplete compressed response")
    except zlib.error:
        raise SyncError("Invalid compressed response") from None
    return body


async def read_json(response, *, maximum=MAX_CONTROLLER_BYTES):
    body = await read_body(response, maximum=maximum)
    return await asyncio.to_thread(_decode, body)


def _decode(body):
    try:
        _check_depth(body)
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise SyncError("Controller returned invalid JSON") from None
