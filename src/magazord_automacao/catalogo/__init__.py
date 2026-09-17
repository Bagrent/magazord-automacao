"""Catalogue generation: offers in, a flipbook and a PDF out.

    from magazord_automacao.catalogo import Opcoes, Tema, construir, de_pasta

    construir(de_pasta(Path("artes")), Tema.carregar(), Opcoes(saida=Path("saida/catalogo")))

The command line wrapper is ``magazord catalogo``.
"""

from .construtor import Opcoes, Resultado, construir
from .fontes_dados import de_pasta, de_planilha, de_produtos
from .oferta import Oferta, moeda
from .tema import Tema

__all__ = [
    "Opcoes", "Resultado", "construir",
    "de_pasta", "de_planilha", "de_produtos",
    "Oferta", "moeda", "Tema",
]
