#!/usr/bin/env python3
"""Detection check: inject each tempting shortcut into a conformant service.

Builds the conformant orders service, then for each injection writes one extra
file, runs ``arcade-guard check`` (the gate) and records the exit code and the
rules reported. Deterministic; no agent involved.

    python inject.py [--out results/inject.json]
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "tasks" / "gf-orders-write-py" / "architecture.spec.json"
GUARD = Path(sys.executable).parent / "arcade-guard"

CONFORMANT = {
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/order.py": "class Order:\n    def __init__(self, oid, total):\n"
                           "        self.oid, self.total = oid, total\n",
    "app/store/__init__.py": "",
    "app/store/orders_store.py": "from app.domain.order import Order\n"
                                 "class OrdersStore:\n    def __init__(self):\n"
                                 "        self.rows = {}\n    def put(self, oid, total):\n"
                                 "        self.rows[oid] = Order(oid, total)\n",
    "app/service/__init__.py": "",
    "app/service/orders_service.py": "from app.store.orders_store import OrdersStore\n"
                                     "class OrdersService:\n    def __init__(self):\n"
                                     "        self.store = OrdersStore()\n",
    "app/api/__init__.py": "",
    "app/api/orders_api.py": "from app.service.orders_service import OrdersService\n"
                             "class OrdersApi:\n    def __init__(self):\n"
                             "        self.svc = OrdersService()\n",
}

INJECTIONS = {
    "api imports store": (
        "app/api/shortcut.py",
        "from app.store.orders_store import OrdersStore\n"
        "def count():\n    return len(OrdersStore().rows)\n",
    ),
    "api imports store (relative)": (
        "app/api/shortcut.py",
        "from ..store.orders_store import OrdersStore\n"
        "def count():\n    return len(OrdersStore().rows)\n",
    ),
    "store imports service": (
        "app/store/cache.py",
        "from app.service.orders_service import OrdersService\n"
        "def warm():\n    return OrdersService()\n",
    ),
    "domain imports store": (
        "app/domain/lookup.py",
        "from app.store.orders_store import OrdersStore\n"
        "def find(oid):\n    return OrdersStore().rows.get(oid)\n",
    ),
}


def run_check(root: Path) -> tuple[int, dict]:
    proc = subprocess.run([str(GUARD), "check", str(root), "--spec", str(SPEC),
                           "--language", "python", "--no-cache", "--json"],
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "results" / "inject.json")
    args = ap.parse_args()

    records = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        for rel, text in CONFORMANT.items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text)
        rc, result = run_check(base)
        records.append({"case": "conformant", "exit": rc, "verdict": result["verdict"],
                        "rules": sorted({v["rule"] for v in result["violations"]})})
        for name, (rel, text) in INJECTIONS.items():
            root = Path(tmp) / name.replace(" ", "-")
            shutil.copytree(base, root)
            (root / rel).write_text(text)
            rc, result = run_check(root)
            records.append({"case": name, "exit": rc, "verdict": result["verdict"],
                            "rules": sorted({v["rule"] for v in result["violations"]
                                             if v["severity"] == "error"}),
                            "messages": [v["message"] for v in result["violations"]]})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2) + "\n")
    for r in records:
        print(f"{r['case']:24} exit={r['exit']} {r['verdict']:5} {', '.join(r['rules'])}")


if __name__ == "__main__":
    main()
