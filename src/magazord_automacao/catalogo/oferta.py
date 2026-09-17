"""One offer -- the content of a single catalogue page."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


def moeda(valor: Decimal | float | int | str | None) -> str | None:
    """Format a price the Brazilian way: ``Decimal("39.9")`` -> ``"39,99"``.

    Strings come back trimmed but otherwise untouched, so a sheet that already
    holds ``"39,99"`` -- or ``"a partir de 39,99"`` -- prints exactly that.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor.strip() or None
    return f"{Decimal(str(valor)):.2f}".replace(".", ",")


@dataclass
class Oferta:
    nome: str
    preco_de: str | None = None
    preco_por: str | None = None
    imagem: Path | None = None
    link: str | None = None
    selo: str | None = None
    observacao: str | None = None
    sku: str | None = None
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def desconto(self) -> int | None:
        """Percentage off, when both prices parse as numbers."""
        def numero(texto: str | None) -> Decimal | None:
            if not texto:
                return None
            limpo = "".join(c for c in texto if c.isdigit() or c in ",.")
            limpo = limpo.replace(".", "").replace(",", ".")
            try:
                return Decimal(limpo)
            except Exception:
                return None

        de, por = numero(self.preco_de), numero(self.preco_por)
        if not de or not por or de <= 0 or por >= de:
            return None
        return int(round((de - por) / de * 100))
