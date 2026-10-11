"""Source connectors: read PDFs from — and write files back to — a file server,
a NAS, object storage or a cloud drive.

Implemented and selectable types:

  local   a folder on this machine (this is also how the cloud *desktop clients*
          appear: iCloud Drive, Google Drive, OneDrive, Dropbox, Synology Drive
          and Nextcloud all expose a local folder — see LOCAL_PRESETS)
  http    an HTTP directory listing (or any page linking PDFs)
  webdav  a WebDAV collection — Nextcloud, ownCloud, Synology, Box, Yandex Disk…
  ssh     a folder on a remote host over SSH/SFTP (system ssh/scp)
  ftp     an FTP/FTPS server (stdlib)
  s3      S3-compatible object storage: AWS S3, MinIO, Cloudflare R2,
          Backblaze B2, Wasabi, DigitalOcean Spaces, Linode (boto3)
  rclone  any of the ~70 providers rclone speaks (Google Drive, OneDrive,
          Dropbox, Box, pCloud, MEGA, SharePoint, Azure Blob, GCS…) — needs the
          rclone binary, which is what makes "all cloud services" practical
  smb     a Windows/SMB share (needs `smbprotocol`)

Each connection can list/scan, fetch (ingest) and **put** (save a document's PDF
or its readable text export back to that destination).

Connections live in `integrations.json` in the launch directory, mode 0600
(it can hold a password or an access key).
"""

from __future__ import annotations

import ftplib
import html.parser
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from . import config

INTEGRATIONS = Path(config.DATA_DIR) / "integrations.json"

# ------------------------------------------------------------------ registry
LOCAL_PRESETS = [
    {"label": "iCloud Drive", "path": "~/Library/Mobile Documents/com~apple~CloudDocs"},
    {"label": "Google Drive (desktop client)", "path": "~/Library/CloudStorage/GoogleDrive-*/My Drive"},
    {"label": "OneDrive (desktop client)", "path": "~/Library/CloudStorage/OneDrive-*"},
    {"label": "Dropbox (desktop client)", "path": "~/Dropbox"},
    {"label": "Synology Drive (desktop client)", "path": "~/SynologyDrive"},
    {"label": "Nextcloud (desktop client)", "path": "~/Nextcloud"},
    {"label": "Google Drive (legacy mount)", "path": "~/Google Drive"},
]


def _has(mod):
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


def connectors():
    """Selectable connection types, grouped, with availability + install hint."""
    return [
        {"id": "local", "label": "Local folder / synced drive", "category": "On this machine",
         "available": True, "note": "a folder, including synced cloud drives",
         "fields": [{"key": "path", "label": "folder path", "placeholder": "~/Documents"}]},

        {"id": "http", "label": "HTTP directory", "category": "Network",
         "available": True, "note": "any page linking .pdf files",
         "fields": [{"key": "url", "label": "url", "placeholder": "https://host.example/docs/"},
                    {"key": "user", "label": "username (optional)"},
                    {"key": "password", "label": "password (optional)", "secret": True}]},

        {"id": "webdav", "label": "WebDAV (Nextcloud, ownCloud, Synology, Box…)", "category": "Network",
         "available": True, "note": "Nextcloud: /remote.php/dav/files/USER/  ·  Synology: /dav/",
         "fields": [{"key": "url", "label": "collection url",
                     "placeholder": "https://cloud.example/remote.php/dav/files/user/pdfs/"},
                    {"key": "user", "label": "username"},
                    {"key": "password", "label": "password", "secret": True}]},

        {"id": "ssh", "label": "SSH / SFTP server folder", "category": "Network",
         "available": bool(shutil.which("ssh")), "install": "install the openssh client",
         "note": "uses your own keys and ~/.ssh/config",
         "fields": [{"key": "host", "label": "host", "placeholder": "192.168.1.57"},
                    {"key": "port", "label": "port", "placeholder": "22"},
                    {"key": "user", "label": "user"},
                    {"key": "path", "label": "remote folder", "placeholder": "/home/user/pdfs"},
                    {"key": "identity", "label": "private key file (optional)",
                     "placeholder": "~/.ssh/id_ed25519"}]},

        {"id": "ftp", "label": "FTP / FTPS server", "category": "Network",
         "available": True, "note": "classic FTP servers and NAS boxes",
         "fields": [{"key": "host", "label": "host"}, {"key": "port", "label": "port", "placeholder": "21"},
                    {"key": "user", "label": "username"},
                    {"key": "password", "label": "password", "secret": True},
                    {"key": "path", "label": "remote folder", "placeholder": "/pub/pdfs"},
                    {"key": "tls", "label": "use TLS (FTPS) — yes/no", "placeholder": "no"}]},

        {"id": "smb", "label": "SMB / Windows share (NAS)", "category": "Network",
         "available": _has("smbclient"), "install": "python3 -m pip install smbprotocol",
         "note": "Synology/QNAP/Windows shares",
         "fields": [{"key": "host", "label": "host"}, {"key": "share", "label": "share name"},
                    {"key": "path", "label": "folder inside the share"},
                    {"key": "user", "label": "username (DOMAIN\\user)"},
                    {"key": "password", "label": "password", "secret": True}]},

        {"id": "s3", "label": "S3-compatible object storage", "category": "Cloud",
         "available": _has("boto3"), "install": "python3 -m pip install boto3",
         "note": "AWS S3 · MinIO · Cloudflare R2 · Backblaze B2 · Wasabi · Spaces · Linode",
         "fields": [{"key": "bucket", "label": "bucket"},
                    {"key": "prefix", "label": "prefix / folder (optional)"},
                    {"key": "endpoint", "label": "endpoint url (optional)",
                     "placeholder": "https://s3.eu-central-1.amazonaws.com"},
                    {"key": "region", "label": "region (optional)"},
                    {"key": "access_key", "label": "access key id (blank = ambient credentials)"},
                    {"key": "secret_key", "label": "secret access key", "secret": True}]},

        {"id": "rclone", "label": "rclone remote (Google Drive, OneDrive, Dropbox, MEGA…)", "category": "Cloud",
         "available": bool(shutil.which("rclone")),
         "install": "brew install rclone   # then: rclone config",
         "note": "one connector for ~70 providers: configure the remote once with `rclone config`",
         "fields": [{"key": "remote", "label": "rclone remote name",
                     "placeholder": "gdrive  (or onedrive, dropbox, mega, box, pcloud…)"},
                    {"key": "path", "label": "folder inside the remote", "placeholder": "pdfs"}]},
    ]


def connector(ctype):
    return next((c for c in connectors() if c["id"] == ctype), None)


def local_presets():
    """Cloud desktop-client folders found on this machine."""
    out = []
    for p in LOCAL_PRESETS:
        expanded = os.path.expanduser(p["path"])
        if "*" in expanded:
            import glob
            hits = sorted(glob.glob(expanded))
            out.append({**p, "resolved": hits[0] if hits else None, "detected": bool(hits)})
        else:
            out.append({**p, "resolved": expanded if os.path.isdir(expanded) else None,
                        "detected": os.path.isdir(expanded)})
    return out


def providers():
    """Catalogue: which connection type to use for a given service."""
    return [
        {"service": "iCloud Drive", "kind": "local", "how": "synced folder (detected on this Mac)"},
        {"service": "Google Drive", "kind": "rclone", "how": "rclone remote `gdrive:`  ·  or the Drive desktop folder"},
        {"service": "OneDrive / SharePoint", "kind": "rclone", "how": "rclone remote `onedrive:` / `sharepoint:`"},
        {"service": "Dropbox", "kind": "rclone", "how": "rclone remote `dropbox:`"},
        {"service": "Box", "kind": "webdav", "how": "https://box.com/dav  ·  or rclone `box:`"},
        {"service": "pCloud", "kind": "webdav", "how": "https://webdav.pcloud.com  ·  or rclone `pcloud:`"},
        {"service": "MEGA", "kind": "rclone", "how": "rclone remote `mega:`"},
        {"service": "Yandex Disk", "kind": "webdav", "how": "https://webdav.yandex.com  ·  or rclone `yandex:`"},
        {"service": "Nextcloud", "kind": "webdav", "how": "/remote.php/dav/files/USER/  ·  or its synced folder"},
        {"service": "ownCloud", "kind": "webdav", "how": "/remote.php/dav/files/USER/"},
        {"service": "Synology Drive", "kind": "webdav", "how": "https://nas:5006/  ·  or the Synology Drive folder"},
        {"service": "AWS S3 / MinIO / R2 / B2 / Wasabi", "kind": "s3", "how": "bucket + endpoint + keys"},
        {"service": "Google Cloud Storage", "kind": "rclone", "how": "rclone remote `gcs:`"},
        {"service": "Azure Blob / Files", "kind": "rclone", "how": "rclone remote `azureblob:` / `azurefiles:`"},
        {"service": "NAS / Windows share", "kind": "smb", "how": "host + share + credentials"},
        {"service": "Any Linux/macOS server", "kind": "ssh", "how": "host + folder, over SSH"},
        {"service": "FTP/FTPS box", "kind": "ftp", "how": "host + credentials"},
        {"service": "Plain web directory", "kind": "http", "how": "url of the listing"},
    ]


# ------------------------------------------------------- saved connections
def load_sources():
    p = Path(INTEGRATIONS)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            pass
    return []


def _write(sources):
    Path(INTEGRATIONS).parent.mkdir(parents=True, exist_ok=True)
    Path(INTEGRATIONS).write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(INTEGRATIONS, 0o600)
    except OSError:
        pass


def save_source(src: dict) -> dict:
    src = {k: (v.strip() if isinstance(v, str) else v) for k, v in src.items() if v is not None}
    if not src.get("name"):
        raise ValueError("a source needs a name")
    if not connector(src.get("type")):
        raise ValueError(f"unknown source type: {src.get('type')}")
    _write([s for s in load_sources() if s.get("name") != src["name"]] + [src])
    return src


def delete_source(name: str):
    _write([s for s in load_sources() if s.get("name") != name])


def get_source(name: str):
    return next((s for s in load_sources() if s.get("name") == name), None)


def public(source: dict) -> dict:
    out = dict(source)
    for f in (connector(source.get("type")) or {}).get("fields", []):
        if f.get("secret") and out.get(f["key"]):
            out[f["key"]] = "••••••"
    return out


# ------------------------------------------------------------------ helpers
def _auth(cfg):
    u, p = cfg.get("user"), cfg.get("password")
    return (u, p) if u else None


def _bool(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "y")


def _ssh_base(cfg):
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=accept-new"]
    if cfg.get("port"):
        args += ["-p", str(cfg["port"])]
    if cfg.get("identity"):
        args += ["-i", str(Path(cfg["identity"]).expanduser())]
    target = f"{cfg.get('user')}@{cfg['host']}" if cfg.get("user") else cfg["host"]
    return args, target


class _Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def _ftp_conn(cfg):
    host = cfg["host"]
    port = int(cfg.get("port") or 21)
    if _bool(cfg.get("tls")):
        c = ftplib.FTP_TLS()
        c.connect(host, port, timeout=30)
        c.login(cfg.get("user") or "anonymous", cfg.get("password") or "")
        c.prot_p()
    else:
        c = ftplib.FTP()
        c.connect(host, port, timeout=30)
        c.login(cfg.get("user") or "anonymous", cfg.get("password") or "")
    return c


def _s3_client(cfg):
    import boto3
    kw = {}
    if cfg.get("endpoint"):
        kw["endpoint_url"] = cfg["endpoint"]
    if cfg.get("region"):
        kw["region_name"] = cfg["region"]
    if cfg.get("access_key"):
        kw["aws_access_key_id"] = cfg["access_key"]
        kw["aws_secret_access_key"] = cfg.get("secret_key") or ""
    return boto3.client("s3", **kw)


# ------------------------------------------------------------------ listing
def _list_local(cfg):
    root = Path(os.path.expanduser(cfg["path"]))
    if not root.is_dir():
        raise FileNotFoundError(f"not a folder: {root}")
    return [{"name": p.name, "remote": str(p), "size": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() == ".pdf"][:2000]


def _list_http(cfg):
    url = cfg["url"]
    r = requests.get(url, timeout=25, auth=_auth(cfg), headers={"User-Agent": "pdfkb/1.0"})
    r.raise_for_status()
    parser = _Links()
    parser.feed(r.text)
    out, seen = [], set()
    for href in parser.hrefs:
        full = urljoin(url, href)
        if full.lower().split("?")[0].endswith(".pdf") and full not in seen:
            seen.add(full)
            out.append({"name": Path(urlparse(full).path).name, "remote": full, "size": 0})
    return out[:2000]


def _list_webdav(cfg):
    url = cfg["url"] if cfg["url"].endswith("/") else cfg["url"] + "/"
    r = requests.request("PROPFIND", url, timeout=25, auth=_auth(cfg),
                         headers={"Depth": "1", "User-Agent": "pdfkb/1.0"})
    r.raise_for_status()
    out, seen = [], set()
    for href in re.findall(r"<(?:[A-Za-z]+:)?href>(.*?)</(?:[A-Za-z]+:)?href>", r.text):
        full = urljoin(url, href)
        if full.lower().endswith(".pdf") and full not in seen and full != url:
            seen.add(full)
            out.append({"name": Path(urlparse(full).path).name, "remote": full, "size": 0})
    return out[:2000]


def _list_ssh(cfg):
    args, target = _ssh_base(cfg)
    remote = (f'for f in $(find "{cfg["path"]}" -maxdepth 4 -type f -iname "*.pdf"); '
              f'do printf "%s\\t%s\\n" "$f" "$(wc -c < "$f")"; done')
    res = subprocess.run(args + [target, remote], capture_output=True, text=True, timeout=90)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "ssh failed").strip()[:400])
    out = []
    for line in res.stdout.splitlines():
        if "\t" not in line:
            continue
        remote_path, size = line.rsplit("\t", 1)
        out.append({"name": Path(remote_path).name, "remote": remote_path,
                    "size": int(size) if size.strip().isdigit() else 0})
    return out[:2000]


def _list_ftp(cfg):
    c = _ftp_conn(cfg)
    try:
        base = cfg.get("path") or "/"
        out = []
        for name, facts in c.mlsd(base):
            if facts.get("type") == "file" and name.lower().endswith(".pdf"):
                out.append({"name": name, "remote": f"{base.rstrip('/')}/{name}",
                            "size": int(facts.get("size") or 0)})
        return out[:2000]
    finally:
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass


def _list_s3(cfg):
    cl = _s3_client(cfg)
    prefix = (cfg.get("prefix") or "").strip("/")
    out = []
    token = None
    while True:
        kw = {"Bucket": cfg["bucket"], "Prefix": (prefix + "/") if prefix else ""}
        if token:
            kw["ContinuationToken"] = token
        resp = cl.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                out.append({"name": Path(obj["Key"]).name, "remote": obj["Key"],
                            "size": int(obj.get("Size") or 0)})
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return out[:2000]


def _list_rclone(cfg):
    target = f"{cfg['remote']}:{(cfg.get('path') or '').strip('/')}"
    res = subprocess.run(["rclone", "lsjson", "-R", "--files-only", target],
                         capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "rclone failed").strip()[:400])
    out = []
    for it in json.loads(res.stdout or "[]"):
        if str(it.get("Path", "")).lower().endswith(".pdf"):
            out.append({"name": it.get("Name") or Path(it["Path"]).name,
                        "remote": it["Path"], "size": int(it.get("Size") or 0)})
    return out[:2000]


LISTERS = {"local": _list_local, "http": _list_http, "webdav": _list_webdav,
           "ssh": _list_ssh, "ftp": _list_ftp, "s3": _list_s3, "rclone": _list_rclone}


def list_pdfs(cfg: dict):
    ctype = cfg.get("type")
    fn = LISTERS.get(ctype)
    if not fn:
        raise RuntimeError(f"'{ctype}' cannot be listed — needs its package/CLI installed")
    return fn(cfg)


# ------------------------------------------------------------------ fetching
def _fetch_local(cfg, remote, dest):
    src = Path(remote)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)


def _fetch_stream(cfg, remote, dest):
    with requests.get(remote, timeout=180, auth=_auth(cfg), stream=True,
                      headers={"User-Agent": "pdfkb/1.0"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)


def _fetch_ssh(cfg, remote, dest):
    _, target = _ssh_base(cfg)
    scp = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if cfg.get("port"):
        scp += ["-P", str(cfg["port"])]
    if cfg.get("identity"):
        scp += ["-i", str(Path(cfg["identity"]).expanduser())]
    res = subprocess.run(scp + [f"{target}:{remote}", str(dest)],
                         capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "scp failed").strip()[:400])


def _fetch_ftp(cfg, remote, dest):
    c = _ftp_conn(cfg)
    try:
        with open(dest, "wb") as fh:
            c.retrbinary(f"RETR {remote}", fh.write)
    finally:
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass


def _fetch_s3(cfg, remote, dest):
    _s3_client(cfg).download_file(cfg["bucket"], remote, str(dest))


def _fetch_rclone(cfg, remote, dest):
    target = f"{cfg['remote']}:{(cfg.get('path') or '').strip('/')}/{remote}"
    res = subprocess.run(["rclone", "copyto", target, str(dest)],
                         capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "rclone failed").strip()[:400])


FETCHERS = {"local": _fetch_local, "http": _fetch_stream, "webdav": _fetch_stream,
            "ssh": _fetch_ssh, "ftp": _fetch_ftp, "s3": _fetch_s3, "rclone": _fetch_rclone}


def fetch(cfg: dict, remote: str, dest_dir) -> Path:
    """Copy one remote PDF into dest_dir; return the local path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(remote).path).name or "download.pdf"
    dest = dest_dir / name
    fn = FETCHERS.get(cfg.get("type"))
    if not fn:
        raise RuntimeError(f"'{cfg.get('type')}' cannot be read — needs its package/CLI installed")
    fn(cfg, remote, dest)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError("nothing was transferred")
    return dest


# -------------------------------------------------------------------- saving
def _put_local(cfg, local, remote):
    root = Path(os.path.expanduser(cfg["path"]))
    dest = root / remote
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local, dest)
    return str(dest)


def _put_webdav(cfg, local, remote):
    base = cfg["url"] if cfg["url"].endswith("/") else cfg["url"] + "/"
    url = urljoin(base, remote)
    with open(local, "rb") as fh:
        r = requests.put(url, data=fh, timeout=300, auth=_auth(cfg),
                         headers={"User-Agent": "pdfkb/1.0"})
    r.raise_for_status()
    return url


def _put_ssh(cfg, local, remote):
    _, target = _ssh_base(cfg)
    scp = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if cfg.get("port"):
        scp += ["-P", str(cfg["port"])]
    if cfg.get("identity"):
        scp += ["-i", str(Path(cfg["identity"]).expanduser())]
    rpath = f"{cfg['path'].rstrip('/')}/{remote}"
    res = subprocess.run(scp + [str(local), f"{target}:{rpath}"],
                         capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "scp failed").strip()[:400])
    return f"{target}:{rpath}"


def _put_ftp(cfg, local, remote):
    c = _ftp_conn(cfg)
    try:
        base = cfg.get("path") or "/"
        with open(local, "rb") as fh:
            c.storbinary(f"STOR {base.rstrip('/')}/{remote}", fh)
        return f"{base.rstrip('/')}/{remote}"
    finally:
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass


def _put_s3(cfg, local, remote):
    prefix = (cfg.get("prefix") or "").strip("/")
    key = f"{prefix}/{remote}" if prefix else remote
    _s3_client(cfg).upload_file(str(local), cfg["bucket"], key)
    return f"s3://{cfg['bucket']}/{key}"


def _put_rclone(cfg, local, remote):
    target = f"{cfg['remote']}:{(cfg.get('path') or '').strip('/')}/{remote}"
    res = subprocess.run(["rclone", "copyto", str(local), target],
                         capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "rclone failed").strip()[:400])
    return target


PUTTERS = {"local": _put_local, "webdav": _put_webdav, "ssh": _put_ssh,
           "ftp": _put_ftp, "s3": _put_s3, "rclone": _put_rclone}


def can_put(cfg: dict) -> bool:
    return cfg.get("type") in PUTTERS


def put(cfg: dict, local_path, remote_name=None) -> str:
    """Save a local file to the source."""
    fn = PUTTERS.get(cfg.get("type"))
    if not fn:
        raise RuntimeError(f"'{cfg.get('type')}' cannot be written to")
    local = Path(local_path)
    if not local.is_file():
        raise FileNotFoundError(str(local))
    return fn(cfg, local, remote_name or local.name)


# -------------------------------------------------------------------- testing
def test(cfg: dict):
    """Connectivity check for a saved or inline config."""
    ctype = cfg.get("type")
    if ctype == "local":
        root = Path(os.path.expanduser(cfg.get("path", "")))
        if not root.is_dir():
            raise FileNotFoundError(f"not a folder: {root}")
        return f"folder ok — {sum(1 for _ in root.glob('**/*.pdf'))} pdf(s) · writable={os.access(root, os.W_OK)}"
    if ctype == "http":
        r = requests.get(cfg["url"], timeout=20, auth=_auth(cfg), headers={"User-Agent": "pdfkb/1.0"})
        return f"http {r.status_code} — {len(r.content)} bytes"
    if ctype == "webdav":
        r = requests.request("PROPFIND", cfg["url"], timeout=20, auth=_auth(cfg),
                             headers={"Depth": "1", "User-Agent": "pdfkb/1.0"})
        return f"webdav {r.status_code} — {len(r.content)} bytes"
    if ctype == "ssh":
        args, target = _ssh_base(cfg)
        res = subprocess.run(args + [target, f'ls -d "{cfg["path"]}" >/dev/null && echo ok'],
                             capture_output=True, text=True, timeout=40)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or "ssh failed").strip()[:400])
        return f"ssh ok — {target}:{cfg['path']}"
    if ctype == "ftp":
        c = _ftp_conn(cfg)
        try:
            c.cwd(cfg.get("path") or "/")
            return f"ftp ok — {cfg.get('host')}:{cfg.get('path') or '/'}"
        finally:
            try:
                c.quit()
            except Exception:  # noqa: BLE001
                pass
    if ctype == "s3":
        cl = _s3_client(cfg)
        resp = cl.list_objects_v2(Bucket=cfg["bucket"], MaxKeys=1)
        return f"s3 ok — bucket={cfg['bucket']} keys={resp.get('KeyCount', 0)}"
    if ctype == "rclone":
        target = f"{cfg['remote']}:"
        res = subprocess.run(["rclone", "lsd", target], capture_output=True, text=True, timeout=90)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or "rclone failed").strip()[:400])
        return f"rclone ok — remote '{cfg['remote']}'"
    if ctype == "smb":
        raise RuntimeError("SMB needs the smbprotocol package: python3 -m pip install smbprotocol")
    raise ValueError(f"unknown source type: {ctype}")
