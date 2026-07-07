import json
import re
import subprocess
import tarfile
from pathlib import Path

PROJECT = Path("/root/ctf-skills")
ROOT = PROJECT / "work/challenges/pwn"
SUMMARY = PROJECT / "work/challenges/pwn-delivery-images.tar.gz"
LOG = PROJECT / "work/challenges/pwn-delivery-images-manifest.json"


def run(cmd):
    print("$", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, check=True, text=True)


def output(cmd):
    return subprocess.check_output(cmd, text=True).splitlines()


image_lines = output(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
images = set(image_lines)
repos = {x.rsplit(":", 1)[0] for x in image_lines if ":" in x}
container_images = set(output(["docker", "ps", "-a", "--format", "{{.Image}}"]))

plan = []
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
    if not ims or not ports:
        missing.append({"dir": str(d.relative_to(PROJECT)), "reason": "missing image/port"})
        continue
    image = ims[0]
    ref = image if ":" in image else image + ":latest"
    if ref not in images and image in repos:
        ref = image + ":latest"
    if ref not in images:
        missing.append({"dir": str(d.relative_to(PROJECT)), "reason": f"missing image {ref}"})
        continue
    host_port = ports[0][0]
    out = d / f"{safe}[{host_port}].tar"
    plan.append({"dir": d, "name": str(name), "image": ref, "port": host_port, "tar": out})

print(f"plan={len(plan)} missing={len(missing)}", flush=True)
entries = []
for item in plan:
    out = item["tar"]
    out_tmp = out.with_suffix(out.suffix + ".tmp")
    if out_tmp.exists():
        out_tmp.unlink()
    print(f"SAVE {item['image']} -> {out}", flush=True)
    run(["docker", "save", "-o", str(out_tmp), item["image"]])
    if out.exists():
        out.unlink()
    out_tmp.rename(out)
    size = out.stat().st_size
    entries.append(
        {
            "dir": str(item["dir"].relative_to(PROJECT)),
            "name": item["name"],
            "port": item["port"],
            "image": item["image"],
            "tar": str(out.relative_to(PROJECT)),
            "bytes": size,
        }
    )
    print(f"WROTE {out} {size}", flush=True)

print(f"COMPRESS {SUMMARY}", flush=True)
summary_tmp = SUMMARY.with_suffix(SUMMARY.suffix + ".tmp")
if summary_tmp.exists():
    summary_tmp.unlink()
with tarfile.open(summary_tmp, "w:gz") as tf:
    for entry in entries:
        path = PROJECT / entry["tar"]
        tf.add(path, arcname=entry["tar"])
if SUMMARY.exists():
    SUMMARY.unlink()
summary_tmp.rename(SUMMARY)

manifest = {"summary": str(SUMMARY.relative_to(PROJECT)), "entries": entries, "missing": missing}
LOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(f"MANIFEST {LOG}", flush=True)

removed = []
skipped = []
for image in sorted({e["image"] for e in entries}):
    if image in container_images:
        skipped.append({"image": image, "reason": "referenced by container"})
        print(f"SKIP_RMI {image} referenced_by_container", flush=True)
        continue
    try:
        run(["docker", "rmi", image])
        removed.append(image)
    except subprocess.CalledProcessError as exc:
        skipped.append({"image": image, "reason": f"rmi failed {exc.returncode}"})
        print(f"SKIP_RMI {image} failed", flush=True)

manifest["removed_images"] = removed
manifest["skipped_rmi"] = skipped
LOG.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(
    f"DONE entries={len(entries)} removed={len(removed)} skipped={len(skipped)} "
    f"summary_bytes={SUMMARY.stat().st_size}",
    flush=True,
)
