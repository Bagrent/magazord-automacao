"""Generate exemplos/modelo_produtos.xlsx.

Thin wrapper -- the real thing lives in the package, so `magazord modelo`
works from an installed copy too.

Run:  python3 exemplos/gerar_modelo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from magazord_automacao.modelo import gerar  # noqa: E402

if __name__ == "__main__":
    print(f"modelo gerado: {gerar(Path(__file__).resolve().parent / 'modelo_produtos.xlsx')}")
