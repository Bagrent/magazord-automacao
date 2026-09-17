"""The printable half of the catalogue.

The flipbook is for reading on a phone; the PDF is what gets attached to an
email, sent to a sales rep, or printed. Both come from the same page images,
so they can never drift apart.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


def gerar(paginas: list[Path], destino: Path, titulo: str = "",
          largura_max: int = 1400, qualidade: int = 88) -> Path:
    """Bundle the page images into one PDF, in order.

    Pages are downscaled to ``largura_max`` first: a catalogue is read on a
    phone or emailed, and twelve full-resolution 1080x1920 frames make a file
    nobody wants to download.
    """
    if not paginas:
        raise ValueError("nenhuma página para gerar o PDF")

    quadros: list[Image.Image] = []
    try:
        for caminho in paginas:
            imagem = Image.open(caminho)
            imagem.load()
            if imagem.mode != "RGB":
                imagem = imagem.convert("RGB")
            if largura_max and imagem.width > largura_max:
                altura = round(imagem.height * largura_max / imagem.width)
                imagem = imagem.resize((largura_max, altura), Image.LANCZOS)
            quadros.append(imagem)

        destino.parent.mkdir(parents=True, exist_ok=True)
        quadros[0].save(
            destino, "PDF", save_all=True, append_images=quadros[1:],
            resolution=150.0, quality=qualidade,
            title=titulo or destino.stem, producer="magazord catalogo",
        )
    finally:
        for quadro in quadros:
            quadro.close()

    log.debug("PDF com %d páginas: %s", len(paginas), destino)
    return destino
