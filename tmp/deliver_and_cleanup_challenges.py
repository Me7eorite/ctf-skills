import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

PROJECT = Path("/root/ctf-skills")
ROOT = PROJECT / "work/challenges/pwn"
ARCHIVE_ROOT = PROJECT / "work/challenges/pwn-delivered-archives"
MANIFEST = ARCHIVE_ROOT / "manifest.json"


def output(cmd):
    return subprocess.check_output(cmd, text=True).splitlines()


def run(cmd, check=True):
    print("$", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, check=check, text=True)


def safe_name(value):
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", str(value)).strip("._")


def challenge_info(directory, images, repos):
    meta = {}
    try:
        meta = json.loads((directory / "metadata.json").read_text())
    except Exception:
        pass

    name = meta.get("name") or meta.get("title") or re.sub(r"^pwn-[^-]+-\d+-", "", directory.name)
    name = str(name)
    comp_path = directory / "deploy/docker-compose.yml"
    comp = comp_path.read_text(errors="ignore") if comp_path.exists() else ""
    image_refs = re.findall(r"(?m)^\s*image:\s*([^\s#]+)", comp)
    ports = re.findall(r"(\d{4,5}):(\d{4,5})", comp)

    image = image_refs[0] if image_refs else ""
    ref = image if ":" in image else f"{image}:latest" if image else ""
    if ref and ref not in images and image in repos:
        ref = f"{image}:latest"

    port = ports[0][0] if ports else "unknown"
    return {
        "name": name,
        "safe_name": safe_name(name) or directory.name,
        "image": ref,
        "image_exists": ref in images,
        "port": port,
    }


def normalize_legacy_tars():
    legacy = ROOT / "pwn-09c5542e-0003-canarytls" / "3_canarytls[9003].tar"
    normalized = ROOT / "pwn-09c5542e-0003-canarytls" / "CanaryTls[9003].tar"
    if legacy.exists() and not normalized.exists():
        legacy.rename(normalized)

    for duplicate in [
        ROOT / "pwn-09c5542e-0004-canary" / "3_cannary[9001].tar",
        ROOT / "pwn-09c5542e-0005-canaryfs" / "canaryfs[9005].tar",
    ]:
        if duplicate.exists():
            duplicate.unlink()


ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
normalize_legacy_tars()

image_lines = output(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
images = set(image_lines)
repos = {line.rsplit(":", 1)[0] for line in image_lines if ":" in line}
container_images = set(output(["docker", "ps", "-a", "--format", "{{.Image}}"]))

entries = []
missing = []
for directory in sorted(path for path in ROOT.iterdir() if path.is_dir()):
    info = challenge_info(directory, images, repos)
    image_tar = directory / f"{info['safe_name']}[{info['port']}].tar"

    if not image_tar.exists():
        if not info["image_exists"]:
            missing.append(
                {
                    "dir": str(directory.relative_to(PROJECT)),
                    "reason": f"missing image tar and docker image {info['image']}",
                }
            )
            continue
        tmp_tar = image_tar.with_suffix(image_tar.suffix + ".tmp")
        if tmp_tar.exists():
            tmp_tar.unlink()
        run(["docker", "save", "-o", str(tmp_tar), info["image"]])
        tmp_tar.rename(image_tar)

    archive = ARCHIVE_ROOT / f"{info['safe_name']}[{info['port']}].tar.gz"
    tmp_archive = archive.with_suffix(archive.suffix + ".tmp")
    if tmp_archive.exists():
        tmp_archive.unlink()
    if archive.exists():
        archive.unlink()

    print(f"ARCHIVE {directory} -> {archive}", flush=True)
    with tarfile.open(tmp_archive, "w:gz") as tf:
        tf.add(directory, arcname=directory.name)
    tmp_archive.rename(archive)

    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())
    if not any(name.endswith("/" + image_tar.name) for name in names):
        raise RuntimeError(f"{archive} does not contain {image_tar.name}")

    entries.append(
        {
            "dir": str(directory.relative_to(PROJECT)),
            "name": info["name"],
            "port": info["port"],
            "image": info["image"],
            "image_exists": info["image_exists"],
            "image_tar": str(image_tar.relative_to(PROJECT)),
            "archive": str(archive.relative_to(PROJECT)),
            "archive_bytes": archive.stat().st_size,
        }
    )

if missing:
    print("MISSING", json.dumps(missing, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(2)

print("REMOVE original folders", flush=True)
for entry in entries:
    shutil.rmtree(PROJECT / entry["dir"])

removed_images = []
skipped_rmi = []
for image in sorted({entry["image"] for entry in entries if entry["image_exists"]}):
    if image in container_images:
        skipped_rmi.append({"image": image, "reason": "referenced by container"})
        print(f"SKIP_RMI {image} referenced_by_container", flush=True)
        continue
    result = run(["docker", "rmi", image], check=False)
    if result.returncode == 0:
        removed_images.append(image)
    else:
        skipped_rmi.append({"image": image, "reason": f"rmi failed {result.returncode}"})

manifest = {
    "archive_root": str(ARCHIVE_ROOT.relative_to(PROJECT)),
    "entries": entries,
    "removed_images": removed_images,
    "skipped_rmi": skipped_rmi,
}
MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(
    f"DONE archives={len(entries)} removed_images={len(removed_images)} "
    f"skipped_rmi={len(skipped_rmi)} manifest={MANIFEST}",
    flush=True,
)
