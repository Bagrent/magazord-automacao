"""Locating the TrueType fonts the catalogue pages are drawn with.

Pillow needs a font *file*, not a family name, and the machine that generates
the catalogue is rarely the machine the art was designed on. So instead of
hardcoding one path, look for a heavy display face and a plain text face among
the usual install locations and fall back through them. A face bundled with
the project wins, then anything the operator installed, then the fonts that
ship with practically every Linux box.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parents[3]

# Searched in order; the first hit wins.
PASTAS = (
    RAIZ / "assets" / "fontes",
    Path.home() / ".fonts",
    Path.home() / ".local" / "share" / "fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("/Library/Fonts"),
    Path("C:/Windows/Fonts"),
)

# Condensed, very bold faces first: the headline and the price are meant to
# shout, and a regular weight blown up to 180px just looks thin.
TITULO = (
    "Anton-Regular.ttf", "Anton.ttf",
    "ArchivoBlack-Regular.ttf", "Archivo-Black.ttf",
    "Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf",
    "Poppins-ExtraBold.ttf", "Poppins-Bold.ttf",
    "Oswald-Bold.ttf", "Impact.ttf",
    "Roboto-Black.ttf", "Roboto-Bold.ttf",
    "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
    "FreeSansBold.ttf", "Arial_Bold.ttf", "arialbd.ttf",
)

TEXTO = (
    "Montserrat-Regular.ttf", "Poppins-Regular.ttf",
    "Roboto-Regular.ttf", "OpenSans-Regular.ttf",
    "LiberationSans-Regular.ttf", "DejaVuSans.ttf",
    "FreeSans.ttf", "Arial.ttf", "arial.ttf",
)


@lru_cache(maxsize=None)
def _indice() -> dict[str, Path]:
    """Map lowercased font file name -> full path, for every font on disk."""
    achados: dict[str, Path] = {}
    for pasta in PASTAS:
        if not pasta.is_dir():
            continue
        for arquivo in pasta.rglob("*"):
            if arquivo.suffix.lower() in {".ttf", ".otf"}:
                achados.setdefault(arquivo.name.lower(), arquivo)
    return achados


def localizar(candidatos: tuple[str, ...]) -> Path | None:
    indice = _indice()
    for nome in candidatos:
        achado = indice.get(nome.lower())
        if achado:
            return achado
    return None


class Fontes:
    """The two faces a page is drawn with, resolved once and cached by size."""

    def __init__(self, titulo: str | Path | None = None, texto: str | Path | None = None):
        self.titulo_path = self._resolver(titulo, TITULO, "título")
        self.texto_path = self._resolver(texto, TEXTO, "texto")
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    @staticmethod
    def _resolver(explicito: str | Path | None, candidatos: tuple[str, ...], papel: str) -> Path | None:
        if explicito:
            caminho = Path(explicito)
            if not caminho.exists():
                raise FileNotFoundError(f"fonte de {papel} não encontrada: {caminho}")
            return caminho
        achado = localizar(candidatos)
        if achado is None:
            log.warning("nenhuma fonte de %s encontrada; usando a fonte embutida do Pillow "
                        "(o resultado fica feio -- instale Anton ou Montserrat)", papel)
        else:
            log.debug("fonte de %s: %s", papel, achado)
        return achado

    def _carregar(self, papel: str, tamanho: int) -> ImageFont.FreeTypeFont:
        chave = (papel, tamanho)
        if chave not in self._cache:
            caminho = self.titulo_path if papel == "titulo" else self.texto_path
            if caminho is None:
                self._cache[chave] = ImageFont.load_default(size=tamanho)
            else:
                self._cache[chave] = ImageFont.truetype(str(caminho), tamanho)
        return self._cache[chave]

    def display(self, tamanho: int) -> ImageFont.FreeTypeFont:
        return self._carregar("titulo", max(1, int(tamanho)))

    def corpo(self, tamanho: int) -> ImageFont.FreeTypeFont:
        return self._carregar("texto", max(1, int(tamanho)))
