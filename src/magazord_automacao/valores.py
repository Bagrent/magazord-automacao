"""Parsing of spreadsheet cell values into typed Python values.

Spreadsheet cells arrive as a mix of ``str``, ``int``, ``float``, ``Decimal``
and ``None`` depending on how the cell was formatted, so every parser here
accepts ``object`` and is defensive about what it gets.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .texto import normalizar


class ValorInvalido(ValueError):
    """A cell could not be parsed into the type its column requires."""


_SO_DIGITOS = re.compile(r"\D")


def vazio(valor: object) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def texto(valor: object) -> str | None:
    if vazio(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        # openpyxl hands back 7891234567890.0 for a numeric barcode cell;
        # str() on that would produce "7891234567890.0".
        return str(int(valor))
    return str(valor).strip()


def decimal(valor: object, formato: str = "br") -> Decimal | None:
    """Parse a monetary or dimensional value.

    ``formato="br"`` reads ``"1.234,56"``; ``formato="en"`` reads ``"1,234.56"``.
    Currency symbols and whitespace are ignored.
    """
    if vazio(valor):
        return None
    if isinstance(valor, (int, Decimal)):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(str(valor))

    bruto = str(valor).strip()
    limpo = re.sub(r"[^\d,.\-]", "", bruto)
    if not limpo or limpo in {"-", ".", ","}:
        raise ValorInvalido(f"não é um número: {bruto!r}")

    if formato == "br":
        # "1.234,56" -> "1234.56"; a lone "1.234" is ambiguous, and Brazilian
        # sheets mean one thousand two hundred thirty-four, so drop the dot.
        limpo = limpo.replace(".", "").replace(",", ".")
    else:
        limpo = limpo.replace(",", "")

    try:
        return Decimal(limpo)
    except InvalidOperation as exc:
        raise ValorInvalido(f"não é um número: {bruto!r}") from exc


def inteiro(valor: object, formato: str = "br") -> int | None:
    numero = decimal(valor, formato)
    if numero is None:
        return None
    if numero != numero.to_integral_value():
        raise ValorInvalido(f"esperado um inteiro, recebido {valor!r}")
    return int(numero)


def booleano(valor: object, verdadeiro: set[str], falso: set[str]) -> bool | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return valor
    chave = normalizar(valor)
    if chave in verdadeiro:
        return True
    if chave in falso:
        return False
    raise ValorInvalido(f"não é sim/não: {valor!r}")


def digitos(valor: object) -> str | None:
    """Strip everything but digits -- for barcodes and NCM codes."""
    limpo = texto(valor)
    if limpo is None:
        return None
    limpo = _SO_DIGITOS.sub("", limpo)
    return limpo or None


def ean_valido(codigo: str) -> bool:
    """Verify a GTIN-8/12/13/14 check digit.

    The last digit is a modulo-10 checksum over the preceding digits, weighted
    3 and 1 alternately from the right. A typo'd barcode fails here rather
    than being silently published to the store.
    """
    if len(codigo) not in (8, 12, 13, 14) or not codigo.isdigit():
        return False
    corpo, esperado = codigo[:-1], int(codigo[-1])
    soma = 0
    for posicao, digito in enumerate(reversed(corpo)):
        soma += int(digito) * (3 if posicao % 2 == 0 else 1)
    return (10 - soma % 10) % 10 == esperado
