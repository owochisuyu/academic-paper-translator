import os
import sys
import time
import shutil
import subprocess

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, "scripts"))
from build import build_xpi

def deploy():
    print("=== Step 1: Building fresh XPI package ===")
    xpi_path = build_xpi()

    profile_dir = r"C:\Users\28048\AppData\Roaming\Zotero\Zotero\Profiles\9dxhzn23.default"
    extensions_dir = os.path.join(profile_dir, "extensions")
    target_xpi = os.path.join(extensions_dir, "antigravity-translator@google.com.xpi")
    bridge_dir = r"C:\Users\28048\AppData\Roaming\Zotero\antigravity-bridge"
    zotero_exe = r"D:\Install\Soft\Zotero\zotero.exe"

    print("\n=== Step 2: Stopping running Zotero instances ===")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "zotero.exe"], capture_output=True, text=True)
        print("  - taskkill zotero.exe executed")
    except Exception as e:
        print(f"  - taskkill note: {e}")

    time.sleep(1.5)

    print("\n=== Step 3: Deploying XPI to Zotero extension directory ===")
    os.makedirs(extensions_dir, exist_ok=True)
    shutil.copy2(xpi_path, target_xpi)
    print(f"  - Successfully copied to {target_xpi}")
    print(f"  - Target file mtime: {time.ctime(os.path.getmtime(target_xpi))}")

    print("\n=== Step 4: Syncing Bridge Python files ===")
    os.makedirs(bridge_dir, exist_ok=True)
    for f in ["fulltext_cli.py", "pdf_extractor.py", "server.py"]:
        src = os.path.join(project_root, "bridge", f)
        dst = os.path.join(bridge_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"  - Synced {f} -> {dst}")

    print("\n=== Step 5: Clearing locks ===")
    for fname in os.listdir(profile_dir):
        if "lock" in fname.lower():
            fpath = os.path.join(profile_dir, fname)
            try:
                os.remove(fpath)
                print(f"  - Removed lock: {fname}")
            except Exception as e:
                print(f"  - Note removing lock {fname}: {e}")

    time.sleep(1.0)

    print("\n=== Step 6: Restarting Zotero ===")
    if os.path.exists(zotero_exe):
        try:
            tn = "ZoteroAutoLaunchTemp"
            subprocess.run(["schtasks", "/create", "/tn", tn, "/tr", zotero_exe, "/sc", "once", "/st", "23:59", "/f"], capture_output=True, text=True)
            subprocess.run(["schtasks", "/run", "/tn", tn], capture_output=True, text=True)
            time.sleep(2.0)
            subprocess.run(["schtasks", "/delete", "/tn", tn, "/f"], capture_output=True, text=True)
            print(f"  - Launched {zotero_exe} via Task Scheduler (independent session)")
        except Exception as e:
            subprocess.Popen([zotero_exe], cwd=os.path.dirname(zotero_exe))
            print(f"  - Fallback launched {zotero_exe}: {e}")
    else:
        print(f"  - Zotero exe not found at {zotero_exe}")

    print("\n=== DEPLOYMENT COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    deploy()

