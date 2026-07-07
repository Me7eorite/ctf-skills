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


ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

# Normalize legacy tar names before archiving the challenge folders.
legacy_9003 = ROOT / "pwn-09c5542e-0003-canarytls" / "3_canarytls[9003].tar"
normalized_9003 = ROOT / "pwn-09c5542e-0003-canarytls" / "CanaryTls[9003].tar"
if legacy_9003.exists() and not normalized_9003.exists():
    legacy_9003.rename(normalized_9003)

for duplicate in [
    ROOT / "pwn-09c5542e-0004-canary" / "3_cannary[9001].tar",
    ROOT / "pwn-09c5542e-0005-canaryfs" / "canaryfs[9005].tar",
]:
    if duplicate.exists():
        duplicate.unlink()

image_lines = output(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
images = set(image_lines)
repos = {x.rsplit(":", 1)[0] for x in image_lines if ":" in x}
container_images = set(output(["docker", "ps", "-a", "--format", "{{.Image}}"]))

entries = []
missing = []
for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
    meta = {}
    try:
        meta = json.loads((d / "metadata.json").read_text())
    except Exception:
        pass
    name = meta.get("name") or meta.get("title") or re.sub(r"^pwn-[^-]+-\d+-", "", d.name)
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(name)).strip("._") or d.name
    comp_path = d / "deploy/docker-compose.yml"
    comp = comp_path.read_text(errors="ignore") if comp_path.exists() else ""
    ims = re.findall(r"(?m)^\s*image:\s*([^\s#]+)", comp)
    ports = re.findall(r"(\d{4,5}):(\d{4,5})", comp)
    host_port = ports[0][0] if ports else "unknown"
    image = ims[0] if ims else ""
    ref = image if ":" in image else image + ":latest" if image else ""
    if ref and ref not in images and image in repos:
        ref = image + ":latest"
    expected_tar = d / f"{safe}[{host_port}].tar"
    if not expected_tar.exists():
        missing.append({"dir": str(d.relative_to(PROJECT)), "reason": f"missing {expected_tar.name}"})
        continue
    archive = ARCHIVE_ROOT / f"{safe}[{host_port}].tar.gz"
    if archive.exists():
        archive.unlink()
    tmp_archive = archive.with_suffix(archive.suffix + ".tmp")
    if tmp_archive.exists():
        tmp_archive.unlink()
    print(f"ARCHIVE {d} -> {archive}", flush=True)
    with tarfile.open(tmp_archive, "w:gz") as tf:
        tf.add(d, arcname=d.name)
    tmp_archive.rename(archive)
    entries.append(
        {
            "dir": str(d.relative_to(PROJECT)),
            "name": str(name),
            "port": host_port,
            "image": ref,
            "image_exists": ref in images,
            "image_tar": str(expected_tar.relative_to(PROJECT)),
            "archive": str(archive.relative_to(PROJECT)),
            "archive_bytes": archive.stat().st_size,
        }
    )

if missing:
    print("MISSING", json.dumps(missing, ensure_ascii=False), flush=True)
    raise SystemExit(2)

print("VERIFY archives", flush=True)
for entry in entries:
    archive = PROJECT / entry["archive"]
    with tarfile.open(archive, "r:gz") as tf:
        names = set(tf.getnames())
    tar_name = Path(entry["image_tar"]).name
    if not any(n.endswith("/" + tar_name) for n in names):
        raise RuntimeError(f"{archive} does not contain {tar_name}")

print("REMOVE original folders", flush=True)
for entry in entries:
    shutil.rmtree(PROJECT / entry["dir"])

removed_images = []
skipped_rmi = []
for image in sorted({e["image"] for e in entries if e["image_exists"]}):
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
