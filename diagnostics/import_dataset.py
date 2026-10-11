"""Import development data from an archive while leaving holdout members sealed."""

import argparse
import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    extracted, sealed = [], []
    with zipfile.ZipFile(args.archive) as archive:
        members = archive.infolist()
        if len(members) > 500 or sum(m.file_size for m in members) > 25_000_000:
            raise ValueError("Dataset archive exceeds import limits")
        targets = set()
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.filename
                or not path.parts
                or path.parts[0] != "freya-datasets-v2"
                or stat.S_ISLNK(mode)
            ):
                raise ValueError("Unsafe archive member")
            target = Path(*path.parts[1:])
            if target in targets:
                raise ValueError("Duplicate archive destination")
            targets.add(target)
        args.out.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(args.archive, args.out / "source.zip")
        for member in members:
            if member.is_dir():
                continue
            relative = Path(*PurePosixPath(member.filename).parts[1:])
            if any("holdout" in part.lower() for part in relative.parts):
                sealed.append(str(relative))
                continue
            target = args.out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            extracted.append(str(relative))
    receipt = {
        "source": str(args.archive.resolve()),
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "extracted_files": extracted,
        "sealed_holdout_files": sealed,
        "holdout_contents_opened": False,
        "author_code_executed": False,
    }
    (args.out / "import-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"extracted": len(extracted), "sealed": len(sealed), "out": str(args.out)}))


if __name__ == "__main__":
    main()
