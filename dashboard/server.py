"""Demo dashboard for the wis2downloader: configuration, subscriptions and the pile of files.

Standard library only. Endpoints:
  /                   the dashboard page
  /api/status         pile size, newest files, purge status, size history
  /api/subscriptions  the downloader's /subscriptions (proxied)
  /api/config         the downloader's config.json and the purge settings
  /files/...          browse and download the downloaded files
  /api/inspect?path=  grib_inspect.py output for a GRIB file, plus its fields
  /api/plot?path=&param=&level=  grib_plot.py map of one field (PNG)

The web server itself uses only the standard library. Inspect and plot run the
grib-utils scripts in grib_utils/, which need earthkit (see Dockerfile).
"""
import hashlib
import html
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DATA = Path(os.environ.get("DOWNLOAD_DIR", "/data/downloads")).resolve()
STATE = Path(os.environ.get("STATE_DIR", "/data/state"))
CONFIG = Path(os.environ.get("DOWNLOADER_CONFIG", "/config/config.json"))
DOWNLOADER = os.environ.get("DOWNLOADER_URL", "http://downloader:5000")
SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "60"))
HISTORY_HOURS = float(os.environ.get("HISTORY_HOURS", "72"))
HISTORY = STATE / "history.json"

GRIB_UTILS = HERE / "grib_utils"
GRIB_SUFFIXES = {".grib", ".grib2", ".grb", ".grb2"}
PLOTS = Path(os.environ.get("PLOT_CACHE", "/tmp/plots"))
# Field shown first: 850 hPa temperature for pressure-level files, 2 m temperature
# for surface files, else mean sea-level pressure, else the file's first field.
PREFERRED = [("t", 850), ("2t", 2), ("msl", 0)]
_plot_slot = threading.Semaphore(1)   # one plot at a time: each needs a few hundred MB
_inspect_cache = {}

_lock = threading.Lock()
_scan_cache = {"t": 0, "v": None}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan():
    """Walk the download directory (cached for a few seconds)."""
    with _lock:
        if _scan_cache["v"] and time.time() - _scan_cache["t"] < 5:
            return _scan_cache["v"]
    n = total = 0
    oldest = newest = None
    latest = []
    by_ext = {}
    for root, _, names in os.walk(DATA):
        for name in names:
            if name.startswith("."):  # hidden files (downloads/.gitkeep) aren't downloads
                continue
            p = Path(root) / name
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            n += 1
            total += st.st_size
            ext = p.suffix.lstrip(".") or "other"
            e = by_ext.setdefault(ext, {"files": 0, "bytes": 0})
            e["files"] += 1
            e["bytes"] += st.st_size
            oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
            latest.append((st.st_mtime, st.st_size, p))
    latest.sort(reverse=True)
    v = {
        "files": n,
        "bytes": total,
        "oldest": iso(oldest) if oldest else None,
        "newest": iso(newest) if newest else None,
        "by_extension": by_ext,
        "latest": [
            {"path": str(p.relative_to(DATA)), "bytes": s, "time": iso(t)}
            for t, s, p in latest[:15]
        ],
    }
    with _lock:
        _scan_cache.update(t=time.time(), v=v)
    return v


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def sampler():
    """Record the pile's size every SAMPLE_SECONDS, for the chart."""
    while True:
        try:
            s = scan()
            hist = read_json(HISTORY, [])
            now = time.time()
            hist.append({"t": iso(now), "files": s["files"], "bytes": s["bytes"]})
            cutoff = iso(now - HISTORY_HOURS * 3600)
            hist = [h for h in hist if h["t"] >= cutoff]
            tmp = HISTORY.with_suffix(".tmp")
            tmp.write_text(json.dumps(hist))
            tmp.replace(HISTORY)
        except Exception as e:
            print(f"sampler error: {e}", flush=True)
        time.sleep(SAMPLE_SECONDS)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def grib_fields(path):
    """(shortName, level, name, typeOfLevel) of every message, from the GRIB headers."""
    import eccodes  # only in the dashboard image, not needed by the rest of the server
    out = []
    with open(path, "rb") as fh:
        while (h := eccodes.codes_grib_new_from_file(fh, headers_only=True)) is not None:
            try:
                out.append(tuple(eccodes.codes_get(h, k)
                                 for k in ("shortName", "level", "name", "typeOfLevel")))
            finally:
                eccodes.codes_release(h)
    return out


def inspect_grib(path):
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    if key in _inspect_cache:
        return _inspect_cache[key]
    run = subprocess.run([sys.executable, str(GRIB_UTILS / "grib_inspect.py"), str(path)],
                         capture_output=True, text=True, timeout=120, cwd="/tmp")
    if run.returncode:
        raise RuntimeError(run.stderr.strip().splitlines()[-1] if run.stderr.strip() else "grib_inspect.py failed")
    params = {}
    for short, level, name, ltype in grib_fields(path):
        p = params.setdefault(short, {"param": short, "name": name, "typeOfLevel": ltype, "levels": []})
        if level not in p["levels"]:
            p["levels"].append(level)
    for p in params.values():
        p["levels"].sort(key=lambda v: -v if p["typeOfLevel"] == "isobaricInhPa" else v)
    default = next(({"param": s, "level": l} for s, l in PREFERRED
                    if s in params and l in params[s]["levels"]), None)
    if default is None and params:
        first = next(iter(params.values()))
        default = {"param": first["param"], "level": first["levels"][0]}
    v = {"text": run.stdout.replace(str(DATA) + "/", ""), "params": list(params.values()),
         "default": default}
    if len(_inspect_cache) > 200:
        _inspect_cache.clear()
    _inspect_cache[key] = v
    return v


def plot_grib(path, param, level):
    st = path.stat()
    h = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}|{param}|{level}".encode()).hexdigest()
    out = PLOTS / f"{h}.png"
    if out.exists():
        return out.read_bytes()
    PLOTS.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(GRIB_UTILS / "grib_plot.py"), str(path), str(out.with_suffix(".tmp.png")),
           "--param", param, "--level", str(level), "--dpi", "110"]
    # grib_plot.py shows fields stored in kelvin in °C by itself
    with _plot_slot:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd="/tmp")
    if run.returncode:
        raise RuntimeError(run.stderr.strip().splitlines()[-1] if run.stderr.strip() else "grib_plot.py failed")
    out.with_suffix(".tmp.png").replace(out)
    for old in sorted(PLOTS.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-100]:
        old.unlink(missing_ok=True)   # keep the 100 newest plots
    return out.read_bytes()


def in_downloads(rel):
    """The path under the download directory, or None if rel points outside it."""
    target = (DATA / rel).resolve()
    return target if target == DATA or DATA in target.parents else None


class Handler(BaseHTTPRequestHandler):
    server_version = "wis2downloader-demo"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=2)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path in ("/api/inspect", "/api/plot"):
            return self.grib(path, urllib.parse.parse_qs(url.query))
        if path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send(200, {
                "now": iso(time.time()),
                "pile": scan(),
                "purge": read_json(STATE / "purge.json", None),
                "history": read_json(HISTORY, []),
            })
        if path == "/api/subscriptions":
            try:
                with urllib.request.urlopen(f"{DOWNLOADER}/subscriptions", timeout=5) as r:
                    return self.send(200, json.loads(r.read()))
            except Exception as e:
                return self.send(502, {"error": f"downloader not reachable: {e}"})
        if path == "/api/config":
            return self.send(200, {
                "downloader": read_json(CONFIG, None),
                "purge": {
                    "retention_hours": float(os.environ.get("RETENTION_HOURS", "36")),
                    "max_download_gb": float(os.environ.get("MAX_DOWNLOAD_GB", "0") or 0),
                    "interval_minutes": float(os.environ.get("PURGE_INTERVAL_MINUTES", "10")),
                },
                # the downloader only queues notifications whose path matches this (patch_filter.py)
                "data_id_filter": os.environ.get("DATA_ID_FILTER", ""),
            })
        if path == "/files" or path.startswith("/files/"):
            return self.files(urllib.parse.unquote(path[len("/files"):]).lstrip("/"))
        return self.send(404, {"error": "not found"})

    def grib(self, endpoint, q):
        target = in_downloads(q.get("path", [""])[0].lstrip("/"))
        if target is None:
            return self.send(403, {"error": "outside the download directory"})
        if target.suffix.lower() not in GRIB_SUFFIXES:
            return self.send(400, {"error": "not a GRIB file"})
        if not target.is_file():
            return self.send(404, {"error": "file not found: it may have been purged"})
        try:
            if endpoint == "/api/inspect":
                return self.send(200, inspect_grib(target))
            param = q.get("param", [""])[0]
            level = q.get("level", [""])[0]
            if not param or not level.lstrip("-").isdigit():
                return self.send(400, {"error": "param and level are required"})
            return self.send(200, plot_grib(target, param, int(level)), "image/png")
        except FileNotFoundError:
            return self.send(404, {"error": "file not found: it may have been purged"})
        except subprocess.TimeoutExpired:
            return self.send(504, {"error": "timed out"})
        except Exception as e:
            return self.send(500, {"error": str(e)})

    def files(self, rel):
        target = in_downloads(rel)
        if target is None:
            return self.send(403, {"error": "outside the download directory"})
        if target.is_file():
            size = target.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.end_headers()
            with target.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    self.wfile.write(chunk)
            return
        if not target.is_dir():
            return self.send(404, "<p>Not found (it may have been purged).</p>", "text/html")
        rows = []
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        for p in entries:
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            href = "/files/" + urllib.parse.quote(str(p.relative_to(DATA)))
            if p.is_dir():
                count = sum(len(f) for _, _, f in os.walk(p))
                info = f"{count} file(s)"
            else:
                info = human(st.st_size)
            name = html.escape(p.name) + ("/" if p.is_dir() else "")
            action = ""
            if p.is_file() and p.suffix.lower() in GRIB_SUFFIXES:
                rel_q = html.escape(str(p.relative_to(DATA)), quote=True)
                action = (f'<button class="look" type="button" data-path="{rel_q}" '
                          f'title="Inspect and plot this GRIB file">{LOOK_ICON}<span>Inspect &amp; plot</span></button>')
            rows.append(f'<tr><td><a href="{href}">{name}</a></td><td class="n">{info}</td>'
                        f'<td>{iso(st.st_mtime)}</td><td>{action}</td></tr>')
        crumbs = ['<a href="/files/">downloads</a>']
        acc = ""
        for part in Path(rel).parts:
            acc = f"{acc}/{part}" if acc else part
            crumbs.append(f'<a href="/files/{urllib.parse.quote(acc)}">{html.escape(part)}</a>')
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>downloads/{html.escape(rel)}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:24px;color:#0b0b0b;background:#fcfcfb}}
 table{{border-collapse:collapse;min-width:640px}} td,th{{padding:4px 12px;border-bottom:1px solid #e6e5e1;text-align:left}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums}} a{{color:#1d62b5}}
 a.back{{display:inline-block;padding:6px 14px;border:1px solid #e1e0db;border-radius:6px;background:#fff;color:#0b0b0b;text-decoration:none;font-weight:500}}
 a.back:hover{{border-color:#1d62b5;color:#1d62b5}}
 @media (prefers-color-scheme:dark){{body{{color:#f0efec;background:#1a1a19}} td,th{{border-color:#33332f}} a{{color:#7fb2f0}}
  a.back{{background:#232321;border-color:#33332f;color:#f0efec}} a.back:hover{{border-color:#7fb2f0;color:#7fb2f0}}}}
{OVERLAY_CSS}
</style></head><body>
<p><a class="back" href="/">&larr; Back to the dashboard</a></p>
<h2>{' / '.join(crumbs)}</h2>
<table><tr><th>Name</th><th>Size</th><th>Modified (UTC)</th><th></th></tr>{''.join(rows) or '<tr><td colspan=4>empty</td></tr>'}</table>
{OVERLAY}
</body></html>"""
        return self.send(200, page, "text/html; charset=utf-8")


LOOK_ICON = ('<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" '
             'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
             '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')

OVERLAY_CSS = """
 button.look{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;font:inherit;font-size:13px;
  border:1px solid #e1e0db;border-radius:6px;background:#fff;color:#0b0b0b;cursor:pointer}
 button.look:hover{border-color:#1d62b5;color:#1d62b5}
 #ov{position:fixed;inset:0;background:rgba(10,10,10,.55);display:none;align-items:center;justify-content:center;z-index:10}
 #ov.open{display:flex}
 #ov .box{background:#fcfcfb;border-radius:10px;width:min(1500px,96vw);height:min(860px,92vh);display:flex;flex-direction:column;
  box-shadow:0 10px 40px rgba(0,0,0,.35);overflow:hidden}
 #ov header{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid #e6e5e1}
 #ov header h3{margin:0;font-size:15px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 #ov header button{font:inherit;padding:4px 12px;border:1px solid #e1e0db;border-radius:6px;background:#fff;color:#0b0b0b;cursor:pointer}
 #ov .cols{flex:1;display:grid;grid-template-columns:minmax(420px,1.2fr) minmax(0,1.4fr);min-height:0}
 #ov pre{margin:0;padding:14px 16px;overflow:auto;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;
  border-right:1px solid #e6e5e1;white-space:pre}
 #ov .plot{display:flex;flex-direction:column;min-height:0;padding:12px 16px;gap:10px}
 #ov .plot label{font-size:13px;margin-right:12px} #ov select{font:inherit;font-size:13px;margin-left:4px;max-width:340px}
 #ov .img{flex:1;display:flex;align-items:center;justify-content:center;min-height:0}
 #ov .img img{max-width:100%;max-height:100%;object-fit:contain;background:#fff;border-radius:4px}
 #ov .msg{color:#6f6e69} #ov .err{color:#b3261e}
 @media (prefers-color-scheme:dark){
  button.look,#ov header button{background:#232321;border-color:#33332f;color:#f0efec}
  button.look:hover{border-color:#7fb2f0;color:#7fb2f0}
  #ov .box{background:#1a1a19} #ov header,#ov pre{border-color:#33332f} #ov .msg{color:#9a998f} #ov .err{color:#f2b8b5}}
"""

OVERLAY = """<div id="ov" role="dialog" aria-modal="true" aria-labelledby="ov-title">
 <div class="box">
  <header><h3 id="ov-title"></h3><button type="button" id="ov-close">Close (Esc)</button></header>
  <div class="cols">
   <pre id="ov-meta" class="msg">Inspecting…</pre>
   <div class="plot">
    <div><label>Parameter<select id="ov-param"></select></label><label>Level<select id="ov-level"></select></label></div>
    <div class="img" id="ov-img"></div>
   </div>
  </div>
 </div>
</div>
<script>
(() => {
  const ov = document.getElementById("ov"), meta = document.getElementById("ov-meta"),
        img = document.getElementById("ov-img"), selP = document.getElementById("ov-param"),
        selL = document.getElementById("ov-level"), title = document.getElementById("ov-title");
  let cur = null, params = [], seq = 0;
  const msg = (el, text, cls) => { el.replaceChildren(); const d = document.createElement("div");
    d.className = cls || "msg"; d.textContent = text; el.append(d); };
  const close = () => { ov.classList.remove("open"); cur = null; seq++; };
  document.getElementById("ov-close").onclick = close;
  ov.addEventListener("click", e => { if (e.target === ov) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && cur) close(); });

  function levels(p) {
    selL.replaceChildren(...p.levels.map(l => new Option(String(l), String(l))));
  }
  async function plot() {
    const my = ++seq, p = selP.value, l = selL.value;
    msg(img, "Plotting " + p + " at level " + l + "… (a few seconds)");
    const r = await fetch("/api/plot?" + new URLSearchParams({path: cur, param: p, level: l}));
    if (my !== seq) return;
    if (!r.ok) { const e = await r.json().catch(() => ({})); return msg(img, "Plot failed: " + (e.error || r.status), "err"); }
    const im = new Image(); im.alt = p + " at level " + l; im.src = URL.createObjectURL(await r.blob());
    img.replaceChildren(im);
  }
  selP.onchange = () => { levels(params.find(x => x.param === selP.value)); plot(); };
  selL.onchange = plot;

  document.querySelectorAll("button.look").forEach(b => b.onclick = async () => {
    cur = b.dataset.path; const my = ++seq;
    title.textContent = cur.split("/").pop();
    meta.className = "msg"; meta.textContent = "Inspecting…"; msg(img, "");
    selP.replaceChildren(); selL.replaceChildren();
    ov.classList.add("open"); document.getElementById("ov-close").focus();
    const r = await fetch("/api/inspect?" + new URLSearchParams({path: cur}));
    if (my !== seq) return;
    const d = await r.json().catch(() => ({error: "bad response"}));
    if (!r.ok) { meta.className = "err"; meta.textContent = "Inspect failed: " + (d.error || r.status); return; }
    meta.className = ""; meta.textContent = d.text; params = d.params;
    selP.replaceChildren(...params.map(p => new Option(p.param + " — " + p.name, p.param)));
    if (!d.default) return msg(img, "No fields to plot.");
    selP.value = d.default.param; levels(params.find(x => x.param === d.default.param));
    selL.value = String(d.default.level); plot();
  });
})();
</script>"""


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=sampler, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    print(f"dashboard on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
