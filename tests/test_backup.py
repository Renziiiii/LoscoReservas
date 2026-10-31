"""Tests del script de backup online de SQLite (deploy/backup.py).

Corre el script real por subprocess (como lo hace el cron del host) contra
bases temporales: verifica copia, verificación de integridad, poda por
--keep y el fallo limpio si la base no existe.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "backup.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _make_db(path: Path, rows: int = 3) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"v{i}",) for i in range(rows)])
    con.commit()
    con.close()


def test_backup_copia_verifica_integridad_y_reporta(tmp_path):
    db = tmp_path / "reservas.db"
    _make_db(db)
    out = tmp_path / "backups"

    r = _run("--db", str(db), "--out", str(out))

    assert r.returncode == 0, r.stderr
    archivos = list(out.glob("reservas-*.db"))
    assert len(archivos) == 1, "debe crear exactamente un backup con fecha"
    con = sqlite3.connect(archivos[0])
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    con.close()
    assert "OK" in r.stdout


def test_backup_conserva_solo_los_ultimos_n(tmp_path):
    db = tmp_path / "reservas.db"
    _make_db(db)
    out = tmp_path / "backups"

    for _ in range(3):
        r = _run("--db", str(db), "--out", str(out), "--keep", "1")
        assert r.returncode == 0, r.stderr

    restantes = list(out.glob("reservas-*.db"))
    assert len(restantes) == 1, "con --keep 1 debe quedar solo el más reciente"


def test_backup_db_inexistente_falla_limpio(tmp_path):
    out = tmp_path / "backups"

    r = _run("--db", str(tmp_path / "no-existe.db"), "--out", str(out))

    assert r.returncode == 1
    assert "no existe" in r.stderr
    assert not out.exists() or not list(out.glob("reservas-*.db")), (
        "no debe crear backups de una base inexistente"
    )
