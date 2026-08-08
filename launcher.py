import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def find_free_port(start=9876, max_tries=100):
    port = start
    for _ in range(max_tries):
        if not is_port_in_use(port):
            return port
        port += 1
    return 0


def run_command(cmd, shell=True, capture=False):
    try:
        if capture:
            r = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=120)
            return r.returncode == 0, r.stdout
        subprocess.check_call(cmd, shell=shell, timeout=120)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except subprocess.CalledProcessError:
        return False, "Command failed"
    except Exception as e:
        return False, str(e)


def is_package_installed(python_exe, package):
    ok, out = run_command(
        f'"{python_exe}" -m pip show "{package}"', capture=True
    )
    return ok


def main():
    root = Path(__file__).parent.absolute()
    os.chdir(root)

    print("--- Scrapling Tool Launcher ---")
    print()

    # --- Virtual environment ---
    venv_path = root / ".venv"
    if os.name == "nt":
        python_exe = venv_path / "Scripts" / "python.exe"
    else:
        python_exe = venv_path / "bin" / "python"

    if not python_exe.exists():
        print("[1/4] Creating virtual environment...")
        ok, err = run_command(f"{sys.executable} -m venv .venv")
        if not ok:
            print(f"Failed to create venv: {err}")
            input("Press Enter to exit...")
            return
    else:
        print("[1/4] Virtual environment ready")

    # Ensure pip is installed
    ok, _ = run_command(f'"{python_exe}" -m pip --version', capture=True)
    if not ok:
        print("pip is missing inside virtual environment, installing...")
        run_command(f'"{python_exe}" -m ensurepip --default-pip', capture=True)

    # --- Install/verify package ---
    already_installed = is_package_installed(python_exe, "scrapling-tool")

    if already_installed:
        print("[2/4] Package already installed, verifying dependencies...")
        ok, _ = run_command(
            f'"{python_exe}" -m pip install -e . --no-deps --quiet', capture=True
        )
    else:
        print("[2/4] Installing package and dependencies...")
        ok, err = run_command(f'"{python_exe}" -m pip install -e .')
        if not ok:
            print(f"Installation failed: {err}")
            input("Press Enter to exit...")
            return

    # --- Ensure playwright browsers ---
    print("[3/4] Checking browser dependencies...")
    ok, _ = run_command(f'"{python_exe}" -m playwright install chromium', capture=True)

    # --- Find free port ---
    # The dashboard (index.html) is hard-coded to 127.0.0.1:8080, so start from there.
    port = find_free_port(8080)
    if port == 0:
        print("Could not find a free port. Exiting.")
        input("Press Enter to exit...")
        return

    # --- Launch server ---
    print(f"[4/4] Starting server on http://127.0.0.1:{port}")
    server_cmd = [
        str(python_exe), "-m", "uvicorn",
        "webapp.server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    process = subprocess.Popen(
        server_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to start (max 15s)
    start_time = time.monotonic()
    while time.monotonic() - start_time < 15:
        if is_port_in_use(port):
            break
        time.sleep(0.3)
    else:
        print("Server startup timed out.")
        process.terminate()
        input("Press Enter to exit...")
        return

    print("Opening dashboard in browser...")
    webbrowser.open(f"http://127.0.0.1:{port}")

    print()
    print("Application is running!")
    print("Close this window or press Ctrl+C to stop.")
    print()

    try:
        process.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        process.terminate()


if __name__ == "__main__":
    main()
