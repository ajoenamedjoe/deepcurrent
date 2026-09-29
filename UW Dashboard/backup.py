"""
Nightly backup of dashboard.db (settings, logo, P&L uploads, journal, track record).

Copied with SQLite's own backup API from INSIDE the running dashboard, which is the
only safe way while it is running: the live data sits in the -wal file, so a plain
file copy gives a stale or empty database (found the hard way -- see the build notes).

Destination: OneDrive\\UW Dashboard Backups (so a dead drive or a lost PC doesn't take
the only copy), falling back to <dashboard>\\backups if OneDrive isn't on this PC.
Keeps the newest KEEP nightly files. Every backup is re-opened and integrity-checked.
"""

import glob
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

KEEP = 30
KEEP_MIN, KEEP_MAX = 3, 365
FOLDER_NAME = "UW Dashboard Backups"
RUN_AFTER_HOUR = 2             # local time; a PC that was off at 2am backs up when it starts


def onedrive_root():
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        p = os.environ.get(var)
        if p and os.path.isdir(p):
            return p
    home = os.path.expanduser("~")
    p = os.path.join(home, "OneDrive")
    return p if os.path.isdir(p) else None


def destination(here, override=None):
    if override:
        return override, "custom folder"
    od = onedrive_root()
    if od:
        return os.path.join(od, FOLDER_NAME), "OneDrive"
    return os.path.join(here, "backups"), "this PC only (OneDrive not found)"


def clamp_keep(v):
    try:
        return max(KEEP_MIN, min(KEEP_MAX, int(v)))
    except (TypeError, ValueError):
        return KEEP


def check_folder(path):
    """Validate a folder chosen for backups. Returns (absolute_path, None) or (None, reason).
    Creates the folder if it doesn't exist and proves it is writable."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        return None, "Choose a folder."
    if re.match(r"^[\\/]{2}", raw):
        return None, "Network shares (\\\\server\\share) aren't allowed. Use a local or OneDrive folder, or a mapped drive letter."
    p = os.path.normpath(os.path.expandvars(os.path.expanduser(raw)))
    if not os.path.isabs(p):
        return None, "Use a full path, like C:\\Backups or D:\\UW Backups."
    if os.path.isfile(p):
        return None, "That is a file, not a folder."
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".uwdash-write-test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        return None, "Can't write there: %s" % (exc.strerror or exc)
    return p, None


def copy_backups(src_dir, dest_dir):
    """Copy existing nightly files into a new folder (never overwriting). Returns how many."""
    if not src_dir or not os.path.isdir(src_dir) or os.path.normcase(os.path.abspath(src_dir)) == os.path.normcase(os.path.abspath(dest_dir)):
        return 0
    n = 0
    for f in glob.glob(os.path.join(src_dir, "dashboard-????-??-??.db")):
        target = os.path.join(dest_dir, os.path.basename(f))
        if not os.path.exists(target):
            shutil.copy2(f, target)
            n += 1
    return n


PICK_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Choose where UW Dashboard saves its backups'
$d.ShowNewFolderButton = $true
$start = $env:UWDASH_PICK_START
if ($start -and (Test-Path -LiteralPath $start)) { $d.SelectedPath = $start }
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }
"""


class PickerUnavailable(Exception):
    pass


def pick_folder(start=None, timeout=600):
    """Open the operating system's folder picker ON THIS PC (the dashboard only ever runs
    locally). Returns the chosen path, or "" if cancelled."""
    if os.name == "nt":
        env = dict(os.environ, UWDASH_PICK_START=start or "")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", PICK_PS],
                               capture_output=True, timeout=timeout, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PickerUnavailable(str(exc))
        return r.stdout.decode("utf-8", "replace").strip()
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        raise PickerUnavailable("No folder picker on this system; type the path instead.")
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(initialdir=start or os.path.expanduser("~"), mustexist=False,
                                       title="Choose where UW Dashboard saves its backups") or ""
    finally:
        root.destroy()


def open_folder(path):
    if not os.path.isdir(path):
        raise OSError("Folder not found: %s" % path)
    if os.name == "nt":
        os.startfile(path)   # noqa: S606 -- local desktop app, path is the backup folder
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def run(db_path, dest_dir, keep=KEEP, stamp=None):
    """Back up now. Returns a dict describing the file written, or raises."""
    os.makedirs(dest_dir, exist_ok=True)
    stamp = stamp or time.strftime("%Y-%m-%d")
    final = os.path.join(dest_dir, "dashboard-%s.db" % stamp)
    tmp = final + ".partial"
    src = sqlite3.connect(db_path, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    chk = sqlite3.connect(tmp)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for t in ("fills", "imports", "journal", "kv", "picks"):
            try:
                counts[t] = chk.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except sqlite3.OperationalError:
                pass
    finally:
        chk.close()
    if ok != "ok":
        os.remove(tmp)
        raise RuntimeError("Backup failed its integrity check: %s" % ok)
    os.replace(tmp, final)
    pruned = prune(dest_dir, keep)
    return {"file": final, "bytes": os.path.getsize(final), "counts": counts, "pruned": pruned,
            "at": time.time()}


def prune(dest_dir, keep=KEEP):
    files = sorted(glob.glob(os.path.join(dest_dir, "dashboard-????-??-??.db")))
    old = files[:-keep] if keep and len(files) > keep else []
    for f in old:
        try:
            os.remove(f)
        except OSError:
            pass
    return len(old)


def listing(dest_dir):
    files = sorted(glob.glob(os.path.join(dest_dir, "dashboard-????-??-??.db")), reverse=True)
    return [{"name": os.path.basename(f), "bytes": os.path.getsize(f), "mtime": os.path.getmtime(f)}
            for f in files]


class Nightly:
    """Background thread: one backup per calendar day, after RUN_AFTER_HOUR."""

    def __init__(self, db_path, here, store, log=print, get_override=lambda: None, get_keep=lambda: KEEP):
        self.db_path, self.here, self.store, self.log = db_path, here, store, log
        self.get_override = get_override
        self.get_keep = get_keep
        self.lock = threading.Lock()

    def status(self):
        dest, where = destination(self.here, self.get_override())
        last = self.store.get("backup_last") or {}
        return {"dest": dest, "where": where, "last": last, "files": listing(dest) if os.path.isdir(dest) else [],
                "keep": clamp_keep(self.get_keep()), "default": destination(self.here)[0]}

    def backup_now(self, why="manual"):
        with self.lock:
            dest, where = destination(self.here, self.get_override())
            try:
                res = run(self.db_path, dest, keep=clamp_keep(self.get_keep()))
                res.update(where=where, why=why, ok=True)
                self.log("backup (%s): %s, %d bytes" % (why, res["file"], res["bytes"]))
            except Exception as exc:   # noqa: BLE001 -- a failed backup is reported, never fatal
                res = {"ok": False, "error": str(exc), "at": time.time(), "where": where, "why": why}
                self.log("backup FAILED: %s" % exc)
            self.store.set("backup_last", res)
            return res

    def due(self, now=None):
        now = now or time.time()
        lt = time.localtime(now)
        last = self.store.get("backup_last") or {}
        if last.get("ok") and time.strftime("%Y-%m-%d", time.localtime(last.get("at", 0))) == time.strftime("%Y-%m-%d", lt):
            return False
        if lt.tm_hour < RUN_AFTER_HOUR:
            # before 2am: only catch up if the last good backup is more than a day old
            return not last.get("ok") or now - last.get("at", 0) > 36 * 3600
        return True

    def run_forever(self, interval=600):
        def loop():
            time.sleep(30)
            while True:
                try:
                    if self.due():
                        self.backup_now("nightly")
                except Exception as exc:   # noqa: BLE001
                    self.log("backup loop error: %s" % exc)
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True, name="backup")
        t.start()
        return t
