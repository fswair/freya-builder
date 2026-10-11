"""Create an explicit public/train diagnostic subset without modifying case content."""

import argparse
import hashlib
import json
from pathlib import Path

from freya_hackathon.builder import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args()
    if args.output.exists() or len(set(args.ids)) != len(args.ids):
        raise ValueError("Output exists or requested IDs are duplicated")
    if not all(case_id.startswith(("PUB-", "DEV-TR-")) for case_id in args.ids):
        raise ValueError("Only public and training cases are allowed")
    cases = json.loads(args.source.read_text())
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("Source IDs are duplicated")
    selected = [by_id[case_id] for case_id in args.ids]
    write_json(args.output, selected)
    write_json(
        args.output.with_suffix(".provenance.json"),
        {
            "source": str(args.source),
            "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
            "selected_ids": args.ids,
            "selection": "Targeted public/train failure diagnosis with a public control case",
            "holdout_opened": False,
            "case_content_modified": False,
        },
    )
    print(args.output)


if __name__ == "__main__":
    main()
