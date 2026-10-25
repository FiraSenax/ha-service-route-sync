"""Bounded JSON decoding for authenticated controller responses."""

import json
import asyncio
import math

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


async def read_json(response, *, maximum=MAX_CONTROLLER_BYTES):
    """Bound decoded bytes even when Content-Length is absent or compressed."""
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        if len(body) + len(chunk) > maximum:
            raise SyncError("Controller response exceeds size limit")
        body.extend(chunk)
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
