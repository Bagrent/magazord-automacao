"""Text normalisation helpers shared by the spreadsheet reader."""

from __future__ import annotations

import re
import unicodedata

_NAO_ALFANUM = re.compile(r"[^a-z0-9]+")
_PARENTESES = re.compile(r"\([^)]*\)")


def normalizar(valor: object) -> str:
    """Fold a header or cell value into a comparable key.

    Strips accents, lowercases, and collapses every run of non-alphanumeric
    characters into a single space. ``"Preço de Venda (R$)"`` and
    ``"PRECO_DE_VENDA_RS"`` both normalise to ``"preco de venda r"``.
    """
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return _NAO_ALFANUM.sub(" ", texto.lower()).strip()


def normalizar_cabecalho(valor: object) -> str:
    """Like :func:`normalizar`, but first drops parenthesised suffixes.

    Spreadsheet headers routinely carry a unit or currency in brackets --
    ``"Preço de Venda (R$)"``, ``"Peso (kg)"``, ``"Altura (cm)"``. Those say
    nothing about which field the column is, and keeping them would force every
    alias list to enumerate the unit spellings too.
    """
    if valor is None:
        return ""
    return normalizar(_PARENTESES.sub(" ", str(valor)))
