"""Local record of what has already been uploaded.

Keyed by SKU, storing the content hash of the last successful upload. A re-run
of the same sheet therefore sends only the products that are new or genuinely
changed -- which is what makes "run this whenever the catalogue is updated"
cheap instead of a full re-upload every time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS enviados (
    sku            TEXT PRIMARY KEY,
    hash_conteudo  TEXT NOT NULL,
    id_magazord    TEXT,
    imagens        INTEGER NOT NULL DEFAULT 0,
    atualizado_em  TEXT NOT NULL
);
"""


class Estado:
    def __init__(self, caminho: Path):
        self.caminho = caminho
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(ESQUEMA)
        self._con.commit()

    def __enter__(self) -> "Estado":
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._con.close()

    def registro(self, sku: str) -> sqlite3.Row | None:
        cur = self._con.execute("SELECT * FROM enviados WHERE sku = ?", (sku,))
        return cur.fetchone()

    def inalterado(self, sku: str, hash_conteudo: str) -> bool:
        reg = self.registro(sku)
        return reg is not None and reg["hash_conteudo"] == hash_conteudo

    def id_conhecido(self, sku: str) -> str | None:
        reg = self.registro(sku)
        return reg["id_magazord"] if reg else None

    def marcar(self, sku: str, hash_conteudo: str, id_magazord: str | None, imagens: int = 0) -> None:
        self._con.execute(
            """
            INSERT INTO enviados (sku, hash_conteudo, id_magazord, imagens, atualizado_em)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                hash_conteudo = excluded.hash_conteudo,
                id_magazord   = COALESCE(excluded.id_magazord, enviados.id_magazord),
                imagens       = excluded.imagens,
                atualizado_em = excluded.atualizado_em
            """,
            (sku, hash_conteudo, id_magazord, imagens,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._con.commit()

    def total(self) -> int:
        return int(self._con.execute("SELECT COUNT(*) FROM enviados").fetchone()[0])

    def listar(self, limite: int = 50) -> list[sqlite3.Row]:
        cur = self._con.execute(
            "SELECT * FROM enviados ORDER BY atualizado_em DESC LIMIT ?", (limite,)
        )
        return cur.fetchall()

    def esquecer(self, sku: str) -> bool:
        cur = self._con.execute("DELETE FROM enviados WHERE sku = ?", (sku,))
        self._con.commit()
        return cur.rowcount > 0
