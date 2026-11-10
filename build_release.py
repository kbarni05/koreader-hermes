#!/usr/bin/env python3
"""Build versioned ZIPs, ZenPM metadata, and the whole-project update feed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
REPOSITORY = "kbarni05/koreader-hermes"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive(name, paths, prefix=""):
    target = DIST / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise ValueError("Do not package symlinks")
            info = zipfile.ZipInfo(prefix + relative, date_time=(2026, 11, 10, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            out.writestr(info, path.read_bytes())
    return target


def source_files():
    names = ["VERSION", "README.md", "CHANGELOG.md", "LICENSE", "build_release.py", ".gitignore", "manifest.json", "versions/hermes.json"]
    files = [ROOT / name for name in names]
    for folder in ("hermes.koplugin", "bridge", "tests"):
        files += [p for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in (".lua", ".py", ".yaml") and "__pycache__" not in p.parts]
    return sorted(set(files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stable", action="store_true", help="Generate stable release metadata rather than preview metadata")
    parser.add_argument("--published-at", help="Fixed UTC ISO-8601 timestamp for reproducible metadata")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must use x.y.z")
    assert (ROOT / "hermes.koplugin/version.lua").read_text() == f'return "{version}"\n'
    for name in ("bridge/hermes_kobo_mcp.py", "hermes.koplugin/_meta.lua"):
        assert f'"{version}"' in (ROOT / name).read_text(), f"Version mismatch in {name}"
    DIST.mkdir(exist_ok=True)
    plugin = sorted((ROOT / "hermes.koplugin").glob("*.lua"))
    plugin_zip = archive(f"hermes-koplugin-v{version}.zip", plugin + [ROOT / "LICENSE"])
    alias = DIST / "hermes.koplugin.zip"
    shutil.copy2(plugin_zip, alias)
    checksum = "sha256:" + hashlib.sha256(plugin_zip.read_bytes()).hexdigest()
    published = args.published_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest = json.loads((ROOT / "manifest.json").read_text())
    package = manifest["packages"][0]
    package.update(version=version if args.stable else "", prerelease_version="" if args.stable else version,
                   source_asset=alias.name, published_at=published, size=str(alias.stat().st_size),
                   description="Manage Kobo files and plugins with Hermes on a NAS; includes verified updates and rollback for the whole project. Separate NAS setup required.")
    write_json(ROOT / "manifest.json", manifest)
    versions_path = ROOT / "versions/hermes.json"
    versions = json.loads(versions_path.read_text())
    versions["releases"] = [r for r in versions["releases"] if r["tag_name"] != "v" + version]
    versions["releases"].insert(0, {
        "tag_name": "v" + version, "name": "v" + version + (" — Preview" if not args.stable else ""), "prerelease": not args.stable,
        "assets": [{"name": alias.name, "size": alias.stat().st_size, "digest": checksum,
                    "url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{alias.name}"}],
    })
    write_json(versions_path, versions)
    files = source_files()

    def entry(path, plugin_entry=False):
        relative = path.relative_to(ROOT).as_posix()
        return {"path": path.name if plugin_entry else relative, "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "url": f"https://raw.githubusercontent.com/{REPOSITORY}/v{version}/{relative}"}

    feed = {"schema_version": 1, "repository": REPOSITORY, "version": version, "preview": not args.stable,
            "plugin": [entry(path, True) for path in plugin], "project": [entry(path) for path in files]}
    write_json(ROOT / "updates/latest.json", feed)
    kit = archive(f"hermes-kobo-nas-kit-v{version}.zip", files + [ROOT / "updates/latest.json"], "koreader-hermes/")
    outputs = [plugin_zip, alias, kit]
    (DIST / "SHA256SUMS").write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in outputs) + "\n")
    for p in outputs:
        print(f"{p.name}: {p.stat().st_size} bytes")
    print(f"Update feed: {len(feed['project'])} managed project files, {len(feed['plugin'])} plugin files")


if __name__ == "__main__":
    main()
