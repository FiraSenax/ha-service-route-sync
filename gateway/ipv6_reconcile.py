#!/usr/bin/python3
"""Local-root-only closeout after an audited IPv6 actor crash or lost reply."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ipv6_actor import CONFIG, JOURNAL, LOCK, atomic_journal, private_json, snapshot
from ipv6_policy import PolicyError, classify, parse_config


def reconcile(transaction_id, digest, mode):
    if os.geteuid() != 0:
        raise PolicyError("root_required")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", transaction_id)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or mode not in ("dsl", "starlink")
    ):
        raise PolicyError("invalid_reconciliation_request")
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = parse_config(private_json(CONFIG))
        journal = private_json(JOURNAL)
        if (
            not isinstance(journal, dict)
            or journal.get("phase") not in ("intent", "blocked")
            or journal.get("transaction_id") != transaction_id
            or journal.get("desired") not in ("dsl", "starlink")
        ):
            raise PolicyError("journal_does_not_match")
        observed = snapshot(config)
        if (
            observed.digest() != digest
            or classify(observed, config) != mode
            or (digest != journal.get("before_digest") and mode != journal["desired"])
        ):
            raise PolicyError("gateway_state_does_not_match")
        atomic_journal(
            {
                "phase": "complete",
                "transaction_id": transaction_id,
                "mode": mode,
                "digest": digest,
                "reconciled": True,
            }
        )
        return {"schema": 1, "reconciled": True, "mode": mode, "digest": digest}
    except BlockingIOError:
        raise PolicyError("busy") from None
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Close an audited IPv6 gateway intent")
    parser.add_argument("transaction_id")
    parser.add_argument("digest")
    parser.add_argument("mode", choices=("dsl", "starlink"))
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                reconcile(args.transaction_id, args.digest, args.mode), separators=(",", ":")
            )
        )
    except PolicyError as error:
        print(json.dumps({"schema": 1, "error_code": str(error)}, separators=(",", ":")))
        sys.exit(1)
