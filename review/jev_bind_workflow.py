"""Bind an existing JEV workflow receipt to its original Workshop transaction.

The binding is a local evidence copy, not a provider signature. It never
changes source, the live workspace, or Workshop state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, help="immutable executed workflow JSON")
    parser.add_argument("--transaction-root", type=Path, required=True)
    args = parser.parse_args()
    source = args.workflow.resolve(strict=True)
    tx_root = args.transaction_root.resolve(strict=True)
    output = tx_root / "review" / "jev_workflow.json"
    state = json.loads((tx_root / "state.json").read_text(encoding="utf-8"))
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("transaction_id") != state.get("transaction_id"):
        parser.error("review transaction does not match the target transaction")
    if data.get("decision") != "APPROVED_FOR_NEXT_ACTION":
        parser.error("review has not passed the JEV workflow")
    if (data.get("attempt_review") or {}).get("mode") != "executed":
        parser.error("review was not produced by a fresh executed request")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(source.read_bytes())
    print(json.dumps({"transaction_id": state["transaction_id"], "bound_receipt": str(output)}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
