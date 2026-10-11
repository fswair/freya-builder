"""Package only the two saved iteration responses requested by Freya support."""

import hashlib
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "diagnostics/freya-mail-20261011/iteration-readings.zip"
SOURCES = {
    "01-first-saved-iteration.json": ROOT / "runs/v12-b-service-canary-001/practice/iteration.json",
    "02-read-at-125354-utc.json": ROOT / "diagnostics/v12-b-canary-stability-002/iteration.json",
}


def main():
    manifest = []
    contents = {}
    for name, path in SOURCES.items():
        raw = path.read_bytes()
        payload = json.loads(raw)
        assert payload["iteration"]["id"] == "d6b8a7f5-7c35-4091-9aef-e0cb2f167fcd"
        assert payload["iteration"]["simulationId"] == "0cd4b78a-470c-4325-94f3-a722db6533f9"
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["id"] == "80b0c948-f463-45fd-948e-0abc1002da95"
        assert not re.search(
            rb'"(?:authorization|api[_-]?key|password|secret|token|cookie|team[_-]?key)"\s*:'
            rb"|Bearer\s+\S+|sk-[A-Za-z0-9_-]{15,}",
            raw,
            re.I,
        ), "Potential credential; review before packaging"
        contents[name] = raw
        manifest.append(
            {"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        )
    contents["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    contents["README.txt"] = (
        "Tamamlanan simülasyonun iki kayıtlı GET yanıtı.\n"
        "01: İlk raporda 11 Ekim 12:37:52 UTC okuması olarak belirtilen kayıt.\n"
        "02: 11 Ekim 12:53:54.050066 UTC tarihinde kaydedilen ikinci okuma.\n"
        "JSON dosyaları yerel kayıtların byte-for-byte kopyasıdır; alanlar değiştirilmedi.\n"
        "İstemci kayıtlarıdır; HTTP başlıkları veya kimlik doğrulama bilgileri dahil değildir.\n"
        "İki sonuç da completed/pass. Değişen alanlar callId ve çağrı zamanlarıdır.\n"
        "Bu simülasyonu iki okuma arasında yeniden başlatmadım.\n"
        "manifest.json dosyası her JSON için SHA-256 ve byte uzunluğunu içerir.\n"
    ).encode()
    with ZipFile(OUT, "x", compression=ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    with ZipFile(OUT) as archive:
        assert archive.testzip() is None
        for name, path in SOURCES.items():
            assert archive.read(name) == path.read_bytes()
    print(json.dumps({"file": str(OUT), "members": list(contents), "bytes": OUT.stat().st_size}))


if __name__ == "__main__":
    main()
