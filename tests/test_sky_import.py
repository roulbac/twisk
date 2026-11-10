"""SkyPilot imports must be safe from concurrent worker threads."""

import subprocess
import sys
import textwrap

# Cold-import SkyPilot from several threads at once, the way the TUI's parallel
# view fetches do. Before the fix this hit "partially initialized module" errors.
SCRIPT = textwrap.dedent(
    """
    import threading
    from twisk.backend import skypilot as b

    errors = []

    def use(get):
        try:
            get()
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    getters = [
        lambda: b._sky().client.sdk.status,
        lambda: b._sky().jobs.queue,
        lambda: b._sky().serve.status,
        lambda: b._sky().volumes.ls,
        lambda: b._sky().status,
        lambda: b._sky().skypilot_config.get_nested,
        lambda: b._sky_cli(),
    ]
    threads = [threading.Thread(target=use, args=(g,)) for g in getters * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("ERRORS:", errors)
    raise SystemExit(1 if errors else 0)
    """
)


def test_concurrent_cold_sky_imports():
    proc = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
