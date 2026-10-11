"""Verify an extracted package's imports and kit wiring without external requests."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

import freya_hackathon
from freya_hackathon.practice import Practice

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    module = Path(freya_hackathon.__file__).resolve()
    if not module.is_relative_to(root):
        raise ValueError("Smoke test imported a different workspace")
    os.environ["FREYA_API_KEY"] = "offline-bundle-smoke-not-a-real-key"
    os.environ["FREYA_BANK_TEAM_KEY"] = "offline-bundle-smoke-not-a-team-key"

    def reject_network(request):
        raise AssertionError("Package smoke must not send network requests")

    client_type = httpx.Client
    transport = httpx.MockTransport(reject_network)

    def offline_client(**kwargs):
        return client_type(transport=transport, **kwargs)

    with patch.object(httpx, "Client", side_effect=offline_client):
        with tempfile.TemporaryDirectory(prefix="freya-bundle-smoke-") as temporary:
            practice = Practice(Path(temporary), 30)
            try:
                assert callable(practice.freya.validate_workflow)
                assert callable(practice.cases.run_once)
                assert callable(practice.cases.score_ledger)
                assert Path(practice.freya.__file__).resolve().is_relative_to(root)
                assert Path(practice.cases.__file__).resolve().is_relative_to(root)
            finally:
                practice.client.close()
    print(
        json.dumps(
            {
                "package": str(root),
                "imports": "passed",
                "practice_kit": "passed",
                "external_requests": 0,
            }
        )
    )
