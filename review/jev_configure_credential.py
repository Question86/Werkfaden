#!/usr/bin/env python3
"""One-time hidden-input setup for the direct TypeSafe Jev credential."""

from __future__ import annotations

import argparse
import getpass

from jev_transport import JevTransportError, credential_target, save_windows_credential


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Store the TypeSafe Jev API key in the current user's Windows Credential Manager."
    )
    parser.add_argument(
        "--target",
        default=credential_target(),
        help="Credential Manager target name (default: configured Kairos target)",
    )
    args = parser.parse_args()

    first = getpass.getpass("TypeSafe Jev API key (hidden input): ")
    second = getpass.getpass("Repeat key once for confirmation (hidden input): ")
    if not first or first != second:
        print("Credential setup aborted: empty input or confirmation mismatch.")
        return 2
    try:
        save_windows_credential(first, args.target)
    except JevTransportError as exc:
        print(f"Credential setup failed: {exc}")
        return 1
    print(f"Stored the Jev credential in Windows Credential Manager under target: {args.target}")
    print("The secret value was not printed or written to the repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
