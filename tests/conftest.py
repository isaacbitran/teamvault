import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture(scope="session")
def demo_server(tmp_path_factory):
    """`teamvault demo` on a free port with the model-free librarian; yields (url, {user: token}, vault path)."""
    root = tmp_path_factory.mktemp("demo")
    port = _free_port()
    env = {**os.environ, "TEAMVAULT_PORT": str(port), "TEAMVAULT_LIBRARIAN": "naive", "PYTHONUNBUFFERED": "1"}
    env.pop("TEAMVAULT_DATA", None); env.pop("TEAMVAULT_CONFIG", None)
    log = open(root / "server.log", "w+")
    proc = subprocess.Popen([sys.executable, "-c", "import teamvault; teamvault.main()", "demo", str(root)],
                            env=env, stdout=log, stderr=subprocess.STDOUT, cwd=root)
    url = f"http://127.0.0.1:{port}"
    tokens = {}
    for _ in range(100):
        time.sleep(0.1)
        text = pathlib.Path(log.name).read_text()
        tokens = {u: line.split()[1] for line in text.splitlines()
                  for u in ("maya", "theo") if line.strip().startswith(u + "  tv_")}
        if len(tokens) == 2:
            try:
                import urllib.request
                urllib.request.urlopen(url + "/health", timeout=1)
                break
            except Exception:
                pass
    else:
        proc.kill(); raise RuntimeError(pathlib.Path(log.name).read_text())
    yield url, tokens, root / "data" / "tenants" / "demo" / "vault"
    proc.terminate(); proc.wait(10)
