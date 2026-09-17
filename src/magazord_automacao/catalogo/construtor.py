"""Turning a source of offers -- or a folder of finished art -- into a catalogue.

One call produces everything the shop needs to publish: the normalised page
images, their thumbnails, a PDF, and the flipbook that reads them. Every step
writes into one output folder, and re-running overwrites that folder's own
generated files, so publishing a new edition is the same command again.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import pdf as pdf_mod
from .arte import Artista, normalizar
from .flipbook import PaginaSaida, gerar as gerar_flipbook
from .fontes import Fontes
from .oferta import Oferta
from .tema import Tema

log = logging.getLogger(__name__)

PASTA_PAGINAS = "paginas"
PASTA_MINIATURAS = "miniaturas"
PADRAO_PAGINA = "pagina-{:02d}.jpg"
LARGURA_MINIATURA = 260


@dataclass
class Opcoes:
    saida: Path = Path("saida/catalogo")
    capa: bool = True
    capa_imagem: Path | None = None
    contracapa: str | None = None
    um_arquivo: bool = False
    gerar_pdf: bool = True
    selo_desconto: bool = False
    qualidade: int = 88
    limite: int | None = None
    rotulo_link: str = "Comprar"


@dataclass
class Resultado:
    pasta: Path
    html: Path
    pdf: Path | None = None
    paginas: list[Path] = field(default_factory=list)
    segundos: float = 0.0

    @property
    def total(self) -> int:
        return len(self.paginas)


def _limpar(pasta: Path) -> None:
    """Drop the pages a previous run wrote, and nothing else."""
    if not pasta.is_dir():
        return
    for antigo in pasta.glob("pagina-*.jpg"):
        antigo.unlink()


def _miniatura(imagem: Image.Image, destino: Path) -> Path:
    altura = max(1, round(imagem.height * LARGURA_MINIATURA / imagem.width))
    pequena = imagem.resize((LARGURA_MINIATURA, altura), Image.LANCZOS)
    pequena.save(destino, "JPEG", quality=74, optimize=True)
    return destino


def construir(fonte: list[Oferta] | list[Path], tema: Tema,
              opcoes: Opcoes | None = None) -> Resultado:
    """Build the whole catalogue.

    ``fonte`` is either a list of :class:`Oferta` -- pages to be drawn -- or a
    list of image paths, art that is already finished and only needs to be
    squared up to the catalogue's page size.
    """
    opcoes = opcoes or Opcoes()
    comeco = time.perf_counter()

    if not fonte:
        raise ValueError("nenhuma oferta ou imagem para montar o catálogo")
    if opcoes.limite:
        fonte = fonte[:opcoes.limite]

    pronta = isinstance(fonte[0], Path)
    pasta = Path(opcoes.saida)
    pasta_paginas = pasta / PASTA_PAGINAS
    pasta_minis = pasta / PASTA_MINIATURAS
    for alvo in (pasta_paginas, pasta_minis):
        alvo.mkdir(parents=True, exist_ok=True)
        _limpar(alvo)

    artista = Artista(tema, Fontes(tema.fonte_titulo or None, tema.fonte_texto or None))
    saidas: list[PaginaSaida] = []
    caminhos: list[Path] = []

    def guardar(imagem: Image.Image, alt: str, link: str | None = None) -> None:
        numero = len(caminhos) + 1
        nome = PADRAO_PAGINA.format(numero)
        destino = pasta_paginas / nome
        imagem.save(destino, "JPEG", quality=opcoes.qualidade, optimize=True,
                    progressive=True)
        mini = _miniatura(imagem, pasta_minis / nome)
        caminhos.append(destino)
        saidas.append(PaginaSaida(arquivo=destino, alt=alt, link=link, miniatura=mini))

    # -- cover -------------------------------------------------------------
    if opcoes.capa and not pronta:
        # With no cover art of its own, the cover shows the first offer's photo.
        foto = opcoes.capa_imagem
        if foto is None:
            foto = next((o.imagem for o in fonte if getattr(o, "imagem", None)), None)
        capa = artista.capa(imagem=foto)
        guardar(capa, f"Capa: {tema.titulo}")
    elif opcoes.capa and opcoes.capa_imagem:
        guardar(normalizar(opcoes.capa_imagem, tema.tamanho), f"Capa: {tema.titulo}")

    # -- body --------------------------------------------------------------
    for item in fonte:
        if pronta:
            caminho = Path(item)                              # type: ignore[arg-type]
            try:
                imagem = normalizar(caminho, tema.tamanho)
            except OSError as exc:
                log.warning("ignorando %s: %s", caminho.name, exc)
                continue
            guardar(imagem, caminho.stem.replace("-", " ").replace("_", " "))
        else:
            oferta: Oferta = item                             # type: ignore[assignment]
            alt = " ".join(p for p in (oferta.nome, oferta.preco_por and
                                       f"por R$ {oferta.preco_por}") if p)
            guardar(artista.oferta(oferta, selo_desconto=opcoes.selo_desconto),
                    alt, oferta.link)

    if not caminhos:
        raise ValueError("nenhuma página pôde ser gerada")

    # -- closing page ------------------------------------------------------
    if opcoes.contracapa:
        guardar(artista.aviso(opcoes.contracapa), opcoes.contracapa)

    # -- PDF + viewer ------------------------------------------------------
    caminho_pdf = None
    if opcoes.gerar_pdf:
        caminho_pdf = pdf_mod.gerar(caminhos, pasta / "catalogo.pdf", titulo=tema.titulo)

    destino_html = (pasta.parent / f"{pasta.name}.html") if opcoes.um_arquivo else pasta
    html = gerar_flipbook(destino_html, saidas, tema, pdf=caminho_pdf,
                          um_arquivo=opcoes.um_arquivo, rotulo_link=opcoes.rotulo_link)

    return Resultado(pasta=pasta, html=html, pdf=caminho_pdf, paginas=caminhos,
                     segundos=time.perf_counter() - comeco)
