#!/usr/bin/env python3
"""Backup online de la base SQLite de LoscoReservas (seguro con la app escribiendo).

Lo corre el cron del host (root) apuntando a la base dentro del volume de
Docker. Usa la API backup() de sqlite3 (los lectores no bloquean escritores
ni al revés), verifica la integridad de la copia y poda los backups viejos.

Ejemplo (crontab del host, diario 04:30):
    30 4 * * * python3 /root/losco-reservas/deploy/backup.py \
        --db /var/lib/docker/volumes/losco-reservas_reservas-data/_data/reservas.db \
        --out /root/backups/reservas --keep 30 >> /var/log/losco-backup.log 2>&1

Restore: apagar la app, copiar el backup sobre la base del volume y levantar
(verbatim en docs/losco-server.md).
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup online de SQLite con verificación y poda."
    )
    parser.add_argument("--db", required=True, help="ruta de la base viva (reservas.db)")
    parser.add_argument("--out", required=True, help="directorio de destino de los backups")
    parser.add_argument(
        "--keep",
        type=int,
        default=30,
        help="cuántos backups conservar (default 30; 0 = sin poda)",
    )
    args = parser.parse_args()

    db = Path(args.db)
    out = Path(args.out)
    if not db.is_file():
        print(f"ERROR: no existe la base {db}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    # Nombre con fecha; sufijo numérico si dos backups caen en el mismo segundo.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out / f"reservas-{stamp}.db"
    n = 1
    while dest.exists():
        dest = out / f"reservas-{stamp}-{n}.db"
        n += 1

    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        resultado = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    # El check abre la copia en modo WAL (el header se hereda de la base
    # origen) y eso crea sidecars -shm/-wal vacíos junto al backup: se
    # limpian para que cada backup sea UN solo archivo autocontenido.
    for sidecar in (Path(f"{dest}-wal"), Path(f"{dest}-shm")):
        sidecar.unlink(missing_ok=True)
    if resultado != "ok":
        print(f"ERROR: integridad de {dest}: {resultado}", file=sys.stderr)
        dest.unlink()
        return 1

    if args.keep > 0:
        backups = sorted(
            out.glob("reservas-*.db"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        for viejo in backups[:-args.keep]:
            viejo.unlink()

    print(f"OK {dest} ({dest.stat().st_size} bytes, integrity ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
