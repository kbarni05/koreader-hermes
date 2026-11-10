#!/usr/bin/env python3
"""Build installable plugin and complete NAS setup kit without local secrets."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"


def archive(name, paths, prefix=""):
    target = DIST / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(prefix + relative, date_time=(2026, 11, 10, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            out.writestr(info, path.read_bytes())
    return target


def main():
    DIST.mkdir(exist_ok=True)
    plugin = list((ROOT / "hermes.koplugin").glob("*.lua"))
    files = plugin + [ROOT / name for name in ("README.md", "LICENSE", "build_release.py", ".gitignore")]
    files += list((ROOT / "bridge").glob("*.py")) + list((ROOT / "bridge").glob("*.yaml"))
    files += list((ROOT / "tests").glob("*.py")) + list((ROOT / "tests").glob("*.lua")) + list((ROOT / "tests/vendor").glob("*.lua"))
    outputs = [archive("hermes-koplugin-v1.0.0.zip", plugin + [ROOT / "LICENSE"]), archive("hermes-kobo-nas-kit-v1.0.0.zip", files, "koreader-hermes/")]
    lines = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in outputs]
    (DIST / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    for p in outputs:
        print(f"{p.name}: {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
