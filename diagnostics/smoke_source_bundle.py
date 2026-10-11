"""Verify installed archive imports and starter-kit wiring without network calls."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import shutil
import tempfile
from pathlib import Path

import httpx

import freya_hackathon
from freya_hackathon.builder import KIT, ROOT, load_inputs, write_json
from freya_hackathon.export_parity import require_preserved
from freya_hackathon.practice import Practice


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    expected = Path.cwd().resolve()
    assert ROOT.resolve() == expected
    modules = []
    for item in pkgutil.walk_packages(freya_hackathon.__path__, prefix="freya_hackathon."):
        module = importlib.import_module(item.name)
        assert Path(module.__file__).resolve().is_relative_to(expected)
        modules.append(item.name.removeprefix("freya_hackathon."))
    os.environ["FREYA_API_KEY"] = "offline-smoke-key"
    os.environ["FREYA_BANK_TEAM_KEY"] = "offline-smoke-team-key"
    requests = []

    def deny_network(self, request, *args, **kwargs):
        requests.append(str(request.url))
        raise AssertionError("Unexpected network call during source smoke check")

    httpx.Client.send = deny_network
    with tempfile.TemporaryDirectory() as directory:
        practice = Practice(Path(directory))
        assert Path(practice.freya.__file__).resolve().is_relative_to(expected)
        assert Path(practice.cases.__file__).resolve().is_relative_to(expected)
        practice.client.close()
    require_preserved({"nodes": {}}, {"nodes": {}})
    directory_inputs = "not present in this source version"
    if "input_documents" in modules:
        with tempfile.TemporaryDirectory() as directory:
            material = Path(directory)
            primary = material / "01-a.md"
            additional = material / "02-b.md"
            shutil.copy2(KIT / "briefs/brief-a.md", primary)
            shutil.copy2(KIT / "briefs/brief-b.md", additional)
            explicit = load_inputs([primary, additional], KIT / "tools/openapi.json")
            expanded = load_inputs([material], KIT / "tools/openapi.json")
            assert expanded == explicit
        directory_inputs = "passed; directory and individual inputs identical"
    result = {
        "package": str(expected),
        "imports": "passed",
        "modules": modules,
        "practice_kit": "passed",
        "export_parity_import": "passed",
        "directory_inputs": directory_inputs,
        "external_requests": len(requests),
    }
    write_json(args.out, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
