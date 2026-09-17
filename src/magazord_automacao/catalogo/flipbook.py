"""Writing the flipbook viewer: one HTML file plus the page images.

The output is static -- no build step, no server, no account. Open
``index.html`` from disk, or drop the folder on any static host (GitHub Pages,
Netlify, the shop's own server) and the link behaves like the commercial
flipbook services, with the catalogue's own images instead of theirs.

``um_arquivo=True`` inlines the CSS, the script and every image as data URIs,
producing a single ``.html`` that can be sent over WhatsApp or e-mail as an
attachment and still opens offline.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from .tema import Tema

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"

_BOTAO_PDF = (
    '<a class="botao" href="{arquivo}" download title="Baixar PDF" aria-label="Baixar PDF">'
    '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg></a>'
)


@dataclass
class PaginaSaida:
    """One rendered page, as the viewer needs to know it."""
    arquivo: Path                 # written under the output folder
    alt: str = ""
    link: str | None = None
    miniatura: Path | None = None


def _dados_uri(caminho: Path) -> str:
    tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
    return f"data:{tipo};base64," + base64.b64encode(caminho.read_bytes()).decode("ascii")


def _relativo(caminho: Path, raiz: Path) -> str:
    return caminho.relative_to(raiz).as_posix()


def _escapar(texto: str) -> str:
    return (texto.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def gerar(destino: Path, paginas: list[PaginaSaida], tema: Tema,
          pdf: Path | None = None, um_arquivo: bool = False,
          rotulo_link: str = "Comprar", descricao: str = "") -> Path:
    """Write the viewer. Returns the path of the HTML file."""
    if not paginas:
        raise ValueError("nenhuma página para montar o flipbook")

    if um_arquivo:
        arquivo = destino if destino.suffix.lower() == ".html" else destino.with_suffix(".html")
        raiz = arquivo.parent
    else:
        raiz, arquivo = destino, destino / "index.html"
    raiz.mkdir(parents=True, exist_ok=True)

    def endereco(caminho: Path) -> str:
        return _dados_uri(caminho) if um_arquivo else _relativo(caminho, raiz)

    itens = []
    for pagina in paginas:
        item: dict[str, str] = {"src": endereco(pagina.arquivo), "alt": pagina.alt}
        if pagina.miniatura is not None:
            item["thumb"] = endereco(pagina.miniatura)
        if pagina.link:
            item["link"] = pagina.link
        itens.append(item)

    dados = {
        "titulo": tema.titulo,
        "proporcao": round(tema.largura / tema.altura, 6),
        "rotulo_link": rotulo_link,
        "paginas": itens,
    }

    css = (ASSETS / "flipbook.css").read_text(encoding="utf-8")
    js = (ASSETS / "flipbook.js").read_text(encoding="utf-8")
    acento = "#%02X%02X%02X" % tema.cor("destaque")
    css = css.replace("--acento: #F07817;", f"--acento: {acento};")

    if um_arquivo:
        estilo = f"<style>\n{css}\n</style>"
        script = f"<script>\n{js}\n</script>"
        botao_pdf = ""          # a data: URI download is unreliable across browsers
    else:
        (raiz / "flipbook.css").write_text(css, encoding="utf-8")
        (raiz / "flipbook.js").write_text(js, encoding="utf-8")
        estilo = '<link rel="stylesheet" href="flipbook.css">'
        script = '<script src="flipbook.js"></script>'
        botao_pdf = _BOTAO_PDF.format(arquivo=_relativo(pdf, raiz)) if pdf else ""

    capa = itens[0].get("thumb") or itens[0]["src"]
    selo = f"{len(paginas)} páginas"
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    substituicoes = {
        "{{TITULO}}": _escapar(tema.titulo),
        "{{DESCRICAO}}": _escapar(descricao or tema.subtitulo or tema.titulo),
        "{{SELO}}": _escapar(selo),
        "{{ACENTO}}": acento,
        "{{CAPA}}": capa,
        "{{FAVICON}}": capa,
        "{{ESTILO}}": estilo,
        "{{SCRIPT}}": script,
        "{{BOTAO_PDF}}": botao_pdf,
        "{{DADOS}}": json.dumps(dados, ensure_ascii=False),
    }
    for chave, valor in substituicoes.items():
        html = html.replace(chave, valor)

    arquivo.write_text(html, encoding="utf-8")
    log.debug("flipbook escrito em %s (%d páginas)", arquivo, len(paginas))
    return arquivo
