"""Turning a source of offers -- or a folder of finished art -- into a catalogue.

One call produces everything the shop needs to publish: the page images, their
thumbnails, a PDF, and the flipbook that reads them. Every step writes into one
output folder, and re-running overwrites that folder's own generated files, so
publishing a new edition is the same command again.

Art that is already finished is copied, never re-encoded. A designer's PNG that
is already the catalogue's page size lands in the output byte for byte: the
generator has nothing to add to it, and a round trip through JPEG would only
throw away detail the designer put there. Re-encoding happens exactly when the
art is the wrong size to begin with, and even then the format is kept, so a PNG
stays a PNG. Source files are never written to.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field, replace
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
PADRAO_PAGINA = "pagina-{:02d}{}"
NOME_DE_PAGINA = re.compile(r"^pagina-\d+$")
LARGURA_MINIATURA = 260
# Formats a browser can show and that we are willing to hand through untouched.
COPIAVEIS = {".png", ".jpg", ".jpeg", ".webp"}


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
    # Finished art goes into the catalogue as it is. Turn this off only to
    # force every page through the JPEG encoder (smaller files, lost detail).
    preservar_originais: bool = True
    # Longest edge of a page inside the PDF. 0 keeps the full resolution.
    pdf_largura: int = 1400
    # With finished art, take the page size from the art itself rather than
    # from the theme, so nothing has to be resized to fit a configured shape.
    tamanho_da_arte: bool = True


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
    """Drop the pages a previous run wrote, and nothing else.

    Matched on the full stem rather than on one extension, because a page keeps
    the extension of the art it came from -- a run that produced .png pages must
    not leave them behind for the next run that produces .jpg.
    """
    if not pasta.is_dir():
        return
    for antigo in pasta.glob("pagina-*"):
        if antigo.is_file() and NOME_DE_PAGINA.match(antigo.stem):
            antigo.unlink()


def _tamanho_dominante(caminhos: list[Path]) -> tuple[int, int] | None:
    """The size most of the art already has -- the catalogue's natural page size."""
    contagem: Counter[tuple[int, int]] = Counter()
    for caminho in caminhos:
        try:
            with Image.open(caminho) as imagem:
                contagem[imagem.size] += 1
        except OSError:
            continue
    return contagem.most_common(1)[0][0] if contagem else None


def _miniatura(imagem: Image.Image, destino: Path) -> Path:
    """A small JPEG for the thumbnail strip -- always derived, never the page."""
    if imagem.mode not in {"RGB", "L"}:
        chapa = Image.new("RGB", imagem.size, (255, 255, 255))
        convertida = imagem.convert("RGBA")
        chapa.paste(convertida, (0, 0), convertida.getchannel("A"))
        imagem = chapa
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
    if pronta and opcoes.tamanho_da_arte:
        # The art decides the page size. Forcing the theme's shape onto pages a
        # designer already exported would resize every one of them for nothing.
        natural = _tamanho_dominante(fonte)                   # type: ignore[arg-type]
        if natural and natural != tema.tamanho:
            log.info("páginas de %dx%d, o tamanho das artes (o tema pedia %dx%d)",
                     *natural, *tema.tamanho)
            tema = replace(tema, largura=natural[0], altura=natural[1])

    pasta = Path(opcoes.saida)
    pasta_paginas = pasta / PASTA_PAGINAS
    pasta_minis = pasta / PASTA_MINIATURAS
    for alvo in (pasta_paginas, pasta_minis):
        alvo.mkdir(parents=True, exist_ok=True)
        _limpar(alvo)

    artista = Artista(tema, Fontes(tema.fonte_titulo or None, tema.fonte_texto or None))
    saidas: list[PaginaSaida] = []
    caminhos: list[Path] = []

    def _registrar(destino: Path, imagem: Image.Image, alt: str, link: str | None) -> None:
        mini = _miniatura(imagem, pasta_minis / f"{destino.stem}.jpg")
        caminhos.append(destino)
        saidas.append(PaginaSaida(arquivo=destino, alt=alt, link=link, miniatura=mini))

    def guardar(imagem: Image.Image, alt: str, link: str | None = None) -> None:
        """Write a page this generator drew."""
        destino = pasta_paginas / PADRAO_PAGINA.format(len(caminhos) + 1, ".jpg")
        imagem.save(destino, "JPEG", quality=opcoes.qualidade, optimize=True,
                    progressive=True)
        _registrar(destino, imagem, alt, link)

    def copiar(origem: Path, alt: str, link: str | None = None) -> None:
        """Take finished art into the catalogue exactly as the designer saved it."""
        destino = pasta_paginas / PADRAO_PAGINA.format(len(caminhos) + 1, origem.suffix.lower())
        shutil.copy2(origem, destino)
        with Image.open(destino) as imagem:
            _registrar(destino, imagem, alt, link)

    def adicionar_arte(caminho: Path, alt: str) -> bool:
        """Put one piece of finished art into the catalogue, as intact as possible."""
        try:
            with Image.open(caminho) as sonda:
                tamanho_original = sonda.size
        except OSError as exc:
            log.warning("ignorando %s: %s", caminho.name, exc)
            return False

        formato_ok = caminho.suffix.lower() in COPIAVEIS
        if (opcoes.preservar_originais and formato_ok
                and tamanho_original == tema.tamanho):
            copiar(caminho, alt)                       # byte for byte
            return True

        try:
            imagem = normalizar(caminho, tema.tamanho)
        except OSError as exc:
            log.warning("ignorando %s: %s", caminho.name, exc)
            return False

        log.info("%s: %dx%d foi ajustada para %dx%d, o tamanho de página do "
                 "catálogo", caminho.name, *tamanho_original, *tema.tamanho)
        if opcoes.preservar_originais and caminho.suffix.lower() == ".png":
            # Resizing is unavoidable at the wrong size; going lossy on top of
            # it is not, so a PNG stays a PNG.
            destino = pasta_paginas / PADRAO_PAGINA.format(len(caminhos) + 1, ".png")
            imagem.save(destino, "PNG", optimize=True)
            _registrar(destino, imagem, alt, None)
        else:
            guardar(imagem, alt)
        return True

    # -- cover -------------------------------------------------------------
    if opcoes.capa and not pronta:
        # With no cover art of its own, the cover shows the first offer's photo.
        foto = opcoes.capa_imagem
        if foto is None:
            foto = next((o.imagem for o in fonte if getattr(o, "imagem", None)), None)
        guardar(artista.capa(imagem=foto), f"Capa: {tema.titulo}")
    elif opcoes.capa and opcoes.capa_imagem:
        adicionar_arte(Path(opcoes.capa_imagem), f"Capa: {tema.titulo}")

    # -- body --------------------------------------------------------------
    for item in fonte:
        if pronta:
            caminho = Path(item)                              # type: ignore[arg-type]
            adicionar_arte(caminho, caminho.stem.replace("-", " ").replace("_", " "))
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
        caminho_pdf = pdf_mod.gerar(caminhos, pasta / "catalogo.pdf", titulo=tema.titulo,
                                    largura_max=opcoes.pdf_largura)

    destino_html = (pasta.parent / f"{pasta.name}.html") if opcoes.um_arquivo else pasta
    html = gerar_flipbook(destino_html, saidas, tema, pdf=caminho_pdf,
                          um_arquivo=opcoes.um_arquivo, rotulo_link=opcoes.rotulo_link)

    return Resultado(pasta=pasta, html=html, pdf=caminho_pdf, paginas=caminhos,
                     segundos=time.perf_counter() - comeco)
