"""
Desk supervisor: is each desk up, start it if not, notice when it changed.

A desk is a folder with a server.py that serves one page on 127.0.0.1:<port>.
The dashboard frames that page, so whatever the desk serves is what the
dashboard shows -- a desk updated on disk is updated on the dashboard.

Two kinds of change are told apart:
  * page change (any top-level .html newer than the one the tab loaded):
    the desks read their page from disk per request, so the dashboard just
    reloads the frame.
  * code change (top-level .py or .env newer than when the process started):
    Python has the old model in memory, so the desk must RESTART to load it.
    The dashboard says so, and restarts it on request (or automatically if
    that setting is on).

Desks are launched with python directly (not START_HERE.bat) with their
"open a browser tab" switch turned off, output going to logs/<desk>.log so
a desk that refuses to start can say why inside the dashboard.
"""

import glob
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import env

DASHBOARD_URL = "http://127.0.0.1:%d" % env.PORT

IS_WIN = os.name == "nt"
LOG_DIR = os.path.join(env.HERE, "logs")

PORT_RE = re.compile(r"""["'](\w*PORT)["']\s*,\s*["']?(\d{4,5})""")
BROWSER_RE = re.compile(r"""["'](\w*OPEN_BROWSER)["']""")
START_TIMEOUT = 45.0          # seconds a desk gets to answer after launch
RETRY_WINDOW = 600.0          # keep-alive: at most MAX_RETRIES launches per window
MAX_RETRIES = 3
SETTLE_SECONDS = 20.0         # code must be unchanged this long before an automatic restart


def resolve(folder):
    if not folder:
        return ""
    folder = os.path.expandvars(os.path.expanduser(folder))
    if re.match(r"^[\\/]{2}", folder):
        return ""                               # never run a desk from a network share
    if os.path.isabs(folder) or re.match(r"^[A-Za-z]:[\\/]", folder):
        return os.path.normpath(folder)
    return os.path.normpath(os.path.join(env.PARENT, folder))


def detect(folder):
    """Read the desk's own source for its port and its open-browser switch."""
    out = {"port": None, "port_var": None, "browser_vars": [], "has_server": False}
    server = os.path.join(folder, "server.py")
    out["has_server"] = os.path.isfile(server)
    texts = []
    for name in ("server.py", "env.py", "uw.py"):
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8", errors="replace") as fh:
                texts.append(fh.read())
        except OSError:
            pass
    src = "\n".join(texts)
    m = PORT_RE.search(texts[0] if texts else "") or PORT_RE.search(src)
    if m:
        out["port_var"], out["port"] = m.group(1), int(m.group(2))
    out["browser_vars"] = sorted(set(BROWSER_RE.findall(src)))
    # the desk's own .env can move its port
    if out["port_var"]:
        val = env.parse_env_file(os.path.join(folder, ".env")).get(out["port_var"])
        if val:
            try:
                out["port"] = int(float(val))
            except ValueError:
                pass
    return out


def stamps(folder):
    """(code_mtime, page_mtime) of the top-level files a desk runs from."""
    code = 0.0
    for p in glob.glob(os.path.join(folder, "*.py")) + [os.path.join(folder, ".env")]:
        try:
            code = max(code, os.path.getmtime(p))
        except OSError:
            pass
    page = 0.0
    for p in glob.glob(os.path.join(folder, "*.html")):
        try:
            page = max(page, os.path.getmtime(p))
        except OSError:
            pass
    return code, page


def probe(port, timeout=1.5):
    """True if anything answers HTTP on 127.0.0.1:port (a 404 still counts)."""
    if not port:
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=timeout) as r:
            r.read(64)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def pid_on_port(port):
    """Windows: the PID listening on 127.0.0.1:port, via netstat. None elsewhere."""
    if not IS_WIN or not port:
        return None
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=10, creationflags=0x08000000).stdout
    except Exception:
        return None
    return parse_netstat(out, port)


def parse_netstat(text, port):
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(":%d" % port):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
    return None


def kill_pid(pid):
    if not pid:
        return False
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=0x08000000)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


class Supervisor:
    """Holds one state dict per desk; a background thread refreshes it."""

    def __init__(self, get_config, log=print, python=None, spawn=None, prober=None):
        self.get_config = get_config          # -> {"desks": [...], "keep_alive": bool, ...}
        self.log = log
        self.python = python or sys.executable
        self.spawn = spawn or self._spawn
        self.prober = prober or probe
        self.lock = threading.RLock()
        self.state = {}
        self.stop_evt = threading.Event()

    # ----------------------------------------------------------- config
    def desks(self):
        out = []
        for d in self.get_config().get("desks", []):
            folder = resolve(d.get("folder", ""))
            det = detect(folder) if folder and os.path.isdir(folder) else {
                "port": None, "port_var": None, "browser_vars": [], "has_server": False}
            port = d.get("port_override") or det["port"] or d.get("port")
            out.append(dict(d, folder_abs=folder, port=port, detected=det,
                            exists=bool(folder) and os.path.isdir(folder)))
        return out

    def _st(self, did):
        return self.state.setdefault(did, {
            "status": "unknown", "proc": None, "pid": None, "started_by_us": False,
            "code_at_start": None, "page_seen": None, "launched_at": None,
            "launches": [], "error": None, "last_probe": None})

    # ----------------------------------------------------------- status
    def refresh(self, only=None):
        cfg = self.get_config()
        for d in self.desks():
            if only and d["id"] != only:
                continue
            with self.lock:
                st = self._st(d["id"])
            self._refresh_one(d, st, cfg)

    def _refresh_one(self, d, st, cfg):
        if not d["exists"] or not d["detected"]["has_server"]:
            st.update(status="missing", error=(
                "Folder not found: %s" % d["folder_abs"] if not d["exists"]
                else "No server.py in %s" % d["folder_abs"]))
            return
        if not d["port"]:
            st.update(status="error", error="Could not find this desk's port in its server.py -- "
                      "set it in Settings.")
            return
        up = self.prober(d["port"])
        code, page = stamps(d["folder_abs"])
        st["last_probe"] = time.time()
        proc = st.get("proc")
        if up:
            if st["status"] != "running":
                st["error"] = None
                if st["code_at_start"] is None:
                    # first time we see it up (someone else started it): the code on
                    # disk right now is the baseline it is presumed to be running
                    st["code_at_start"] = code
            st["status"] = "running"
            st["page_mtime"] = page
            st["code_mtime"] = code
            st["updated"] = bool(st["code_at_start"] and code > st["code_at_start"] + 1)
            # wait until the files have been quiet for a while: a deploy writes files one
            # by one, and restarting halfway through would load a half-new desk
            if st["updated"] and cfg.get("auto_restart_on_update") and time.time() - code > SETTLE_SECONDS:
                self.log("%s: code changed on disk -- restarting to load it" % d["name"])
                self.restart(d["id"])
            return
        # not answering
        rc = proc.poll() if proc is not None else None
        if rc is not None:
            st.update(status="error", proc=None,
                      error="The desk stopped by itself (exit code %s). The end of its log is below." % rc)
        elif st["status"] == "starting" and st["launched_at"] and time.time() - st["launched_at"] < START_TIMEOUT:
            pass
        elif st["status"] == "starting":
            st.update(status="error", error="Started but did not answer on port %d within %ds."
                      % (d["port"], START_TIMEOUT))
        elif st["status"] != "error":
            st["status"] = "stopped"
        if st["status"] != "starting":
            st["code_at_start"] = None
            st["started_by_us"] = False
        st["updated"] = False
        want = d.get("autostart") or cfg.get("keep_alive")
        if want and st["status"] in ("stopped", "error") and self._may_retry(st):
            self.start(d["id"], reason="keep-alive")

    def _may_retry(self, st):
        now = time.time()
        st["launches"] = [t for t in st["launches"] if now - t < RETRY_WINDOW]
        return len(st["launches"]) < MAX_RETRIES

    def snapshot(self):
        out = []
        for d in self.desks():
            with self.lock:
                st = self._st(d["id"])
            out.append({
                "id": d["id"], "name": d["name"], "folder": d["folder_abs"], "port": d["port"],
                "url": "http://127.0.0.1:%d/" % d["port"] if d["port"] else None,
                "status": st["status"], "error": st.get("error"), "updated": st.get("updated", False),
                "page_mtime": st.get("page_mtime"), "started_by_us": st["started_by_us"],
                "autostart": bool(d.get("autostart")), "port_var": d["detected"]["port_var"],
                "log_tail": self.log_tail(d["id"]) if st["status"] in ("error",) else None,
            })
        return out

    # ----------------------------------------------------------- control
    def ensure(self, did):
        """Start the desk if it is not running. Returns the new status."""
        self.refresh(only=did)
        with self.lock:
            st = self._st(did)
            if st["status"] in ("running", "starting"):
                return st["status"]
        return self.start(did, reason="opened")

    def start(self, did, reason="manual"):
        d = next((x for x in self.desks() if x["id"] == did), None)
        if not d:
            return "missing"
        with self.lock:
            st = self._st(did)
            if not d["exists"] or not d["detected"]["has_server"] or not d["port"]:
                self._refresh_one(d, st, self.get_config())
                return st["status"]
            if self.prober(d["port"]):
                st["status"] = "running"
                return "running"
            try:
                proc = self.spawn(d)
            except Exception as exc:          # noqa: BLE001 -- report, never crash the hub
                st.update(status="error", error="Could not launch: %s" % exc)
                return "error"
            code, _ = stamps(d["folder_abs"])
            st.update(status="starting", proc=proc, pid=getattr(proc, "pid", None), started_by_us=True,
                      code_at_start=code, launched_at=time.time(), error=None, updated=False)
            st["launches"].append(time.time())
        self.log("%s: starting (%s) on port %s" % (d["name"], reason, d["port"]))
        return "starting"

    def stop(self, did):
        d = next((x for x in self.desks() if x["id"] == did), None)
        if not d:
            return False
        with self.lock:
            st = self._st(did)
            pid = st.get("pid") if st.get("proc") is not None and st["proc"].poll() is None else None
            pid = pid or pid_on_port(d["port"])
            ok = kill_pid(pid)
            st.update(status="stopped", proc=None, pid=None, updated=False, code_at_start=None)
        # wait for the port to free up
        for _ in range(20):
            if not self.prober(d["port"]):
                break
            time.sleep(0.25)
        self.log("%s: stopped (pid %s)" % (d["name"], pid))
        return ok

    def restart(self, did):
        self.stop(did)
        return self.start(did, reason="restart")

    # ----------------------------------------------------------- process
    def _spawn(self, d):
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "%s.log" % d["id"])
        try:
            if os.path.getsize(path) > 5_000_000:
                os.replace(path, path + ".1")
        except OSError:
            pass
        logf = open(path, "a", encoding="utf-8", errors="replace")
        logf.write("\n===== %s  started by the dashboard =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        logf.flush()
        child_env = dict(os.environ)
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["UW_DASHBOARD_URL"] = DASHBOARD_URL   # desks ask it what to call themselves (brandname.py)
        for var in d["detected"]["browser_vars"]:
            child_env[var] = "0"              # the dashboard is the browser tab
        kwargs = dict(cwd=d["folder_abs"], env=child_env, stdout=logf, stderr=subprocess.STDOUT,
                      stdin=subprocess.DEVNULL)
        if IS_WIN:
            # no console window, own process group: survives the dashboard closing,
            # and Ctrl+C in the dashboard window does not take the desks with it
            kwargs["creationflags"] = 0x08000000 | 0x00000200
        return subprocess.Popen([self.python, "server.py"], **kwargs)

    def log_tail(self, did, lines=25):
        path = os.path.join(LOG_DIR, "%s.log" % did)
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 8000))
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            return None
        return "\n".join(text.splitlines()[-lines:])

    # ----------------------------------------------------------- loop
    def run(self, interval=5.0):
        def loop():
            while not self.stop_evt.is_set():
                try:
                    self.refresh()
                except Exception as exc:      # noqa: BLE001
                    self.log("supervisor error: %s" % exc)
                self.stop_evt.wait(interval)
        t = threading.Thread(target=loop, daemon=True, name="desk-supervisor")
        t.start()
        return t
