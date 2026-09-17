"""Where the catalogue's pages come from.

Three sources, because three things are true at once: the shop already has art
for some campaigns, wants the rest generated from a price list, and keeps the
product data in the same workbook the uploader reads.

* :func:`de_pasta`    -- ready-made art (PNG/JPG) exported by a designer
* :func:`de_planilha` -- an offers sheet (.xlsx/.csv): one row, one page
* :func:`de_produtos` -- the product workbook this project already uses

All three hand back ``Oferta`` objects (or, for ready-made art, plain paths),
so the rest of the pipeline does not care which one was used.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from ..texto import normalizar_cabecalho
from .oferta import Oferta, moeda

log = logging.getLogger(__name__)

EXTENSOES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

# Header alias -> field. Matched after accent/case folding, longest first.
COLUNAS: dict[str, tuple[str, ...]] = {
    "nome": ("produto", "nome", "nome do produto", "titulo", "descricao", "item"),
    "preco_de": ("preco de", "de", "preco original", "preco cheio", "preco antigo",
                 "valor de", "preco"),
    "preco_por": ("preco por", "por", "preco promocional", "preco promo", "promocao",
                  "valor por", "oferta"),
    "imagem": ("imagem", "imagem 1", "foto", "arquivo", "url da imagem", "link da imagem"),
    "link": ("link", "url", "url do produto", "link do produto", "pagina do produto"),
    "selo": ("selo", "etiqueta", "tag", "chamada"),
    "observacao": ("observacao", "obs", "detalhe", "nota"),
    "sku": ("sku", "codigo", "cod", "referencia"),
}

_NUMERO = re.compile(r"(\d+)")


def ordem_natural(caminho: Path) -> tuple[Any, ...]:
    """Sort key where ``pagina2`` comes before ``pagina10``."""
    partes = _NUMERO.split(caminho.name.lower())
    return tuple(int(p) if p.isdigit() else p for p in partes)


def de_pasta(pasta: Path) -> list[Path]:
    """Every image in ``pasta``, in natural filename order.

    Filenames are the running order, so ``01-capa.jpg``, ``02-base.jpg`` ...
    is all the sequencing the operator has to do.
    """
    if not pasta.is_dir():
        raise NotADirectoryError(f"pasta de imagens não encontrada: {pasta}")
    arquivos = [c for c in pasta.iterdir()
                if c.is_file() and c.suffix.lower() in EXTENSOES and not c.name.startswith(".")]
    if not arquivos:
        raise FileNotFoundError(
            f"nenhuma imagem em {pasta} "
            f"(aceitas: {', '.join(sorted(EXTENSOES))})"
        )
    return sorted(arquivos, key=ordem_natural)


# -- offer sheets ----------------------------------------------------------

def _mapear_cabecalho(cabecalho: Iterable[object]) -> dict[int, str]:
    """Column index -> field name, by alias."""
    por_alias: dict[str, str] = {}
    for campo, aliases in COLUNAS.items():
        for alias in aliases:
            por_alias.setdefault(normalizar_cabecalho(alias), campo)

    achados: dict[int, str] = {}
    usados: set[str] = set()
    for i, celula in enumerate(cabecalho):
        chave = normalizar_cabecalho(celula)
        campo = por_alias.get(chave)
        # "Preço" alone means the current price when there is no "Preço por".
        if campo and campo not in usados:
            achados[i] = campo
            usados.add(campo)
    return achados


def _linhas_xlsx(caminho: Path) -> list[list[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(caminho, read_only=True, data_only=True)
    try:
        ws = wb.active
        return [list(linha) for linha in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _linhas_csv(caminho: Path) -> list[list[Any]]:
    texto = caminho.read_text(encoding="utf-8-sig")
    dialeto = csv.Sniffer().sniff(texto[:2048], delimiters=";,\t") if texto.strip() else csv.excel
    return [list(l) for l in csv.reader(texto.splitlines(), dialeto)]


def de_planilha(caminho: Path, base_imagens: Path | None = None) -> list[Oferta]:
    """Read an offers sheet: one row per page.

    Only ``Produto`` is required. An image path is resolved relative to the
    sheet itself, so the usual layout -- sheet plus a ``fotos/`` folder beside
    it -- just works.
    """
    caminho = Path(caminho)
    linhas = _linhas_csv(caminho) if caminho.suffix.lower() == ".csv" else _linhas_xlsx(caminho)
    linhas = [l for l in linhas if any(c not in (None, "") for c in l)]
    if not linhas:
        raise ValueError(f"{caminho} está vazia")

    colunas = _mapear_cabecalho(linhas[0])
    if "nome" not in colunas.values():
        raise ValueError(
            f"{caminho}: não achei a coluna do nome do produto. "
            f"Use um cabeçalho como {', '.join(COLUNAS['nome'][:3])}."
        )

    raiz = Path(base_imagens) if base_imagens else caminho.parent
    ofertas: list[Oferta] = []
    for numero, linha in enumerate(linhas[1:], start=2):
        valores = {campo: linha[i] for i, campo in colunas.items() if i < len(linha)}
        nome = valores.get("nome")
        nome = str(nome).strip() if nome not in (None, "") else ""
        if not nome:
            continue

        bruto_img = valores.get("imagem")
        imagem = str(bruto_img).strip() if bruto_img not in (None, "") else ""
        ofertas.append(Oferta(
            nome=nome,
            preco_de=moeda(valores.get("preco_de")),
            preco_por=moeda(valores.get("preco_por")),
            imagem=_resolver_imagem(imagem, raiz, numero),
            link=(str(valores["link"]).strip() if valores.get("link") else None),
            selo=(str(valores["selo"]).strip() if valores.get("selo") else None),
            observacao=(str(valores["observacao"]).strip() if valores.get("observacao") else None),
            sku=(str(valores["sku"]).strip() if valores.get("sku") else None),
        ))

    if not ofertas:
        raise ValueError(f"{caminho}: nenhuma linha com produto preenchido")
    return ofertas


def _resolver_imagem(valor: str, raiz: Path, linha: int) -> Path | None:
    if not valor:
        return None
    if valor.lower().startswith(("http://", "https://")):
        return _baixar(valor, raiz / ".cache_catalogo")
    caminho = Path(valor)
    if not caminho.is_absolute():
        caminho = raiz / caminho
    if not caminho.exists():
        log.warning("linha %d: imagem não encontrada em %s", linha, caminho)
        return None
    return caminho


def _baixar(url: str, cache: Path) -> Path | None:
    """Fetch a remote image once and keep it, so re-runs are offline."""
    import httpx

    cache.mkdir(parents=True, exist_ok=True)
    sufixo = Path(url.split("?")[0]).suffix.lower()
    if sufixo not in EXTENSOES:
        sufixo = ".img"
    destino = cache / (hashlib.sha1(url.encode()).hexdigest()[:16] + sufixo)
    if destino.exists() and destino.stat().st_size:
        return destino
    try:
        resposta = httpx.get(url, timeout=30, follow_redirects=True)
        resposta.raise_for_status()
    except Exception as exc:                      # noqa: BLE001 - one bad URL, one skipped image
        log.warning("não foi possível baixar %s: %s", url, exc)
        return None
    destino.write_bytes(resposta.content)
    return destino


# -- the product workbook this project already reads -----------------------

def de_produtos(caminho: Path, mapeamento: dict[str, Any],
                somente_promocao: bool = True) -> list[Oferta]:
    """Turn the uploader's own product sheet into catalogue offers.

    ``somente_promocao`` keeps the rows that actually have a promotional price
    -- a catalogue of full-price items is not a promotion catalogue.
    """
    from ..planilha import LeitorPlanilha

    produtos, erros = LeitorPlanilha(mapeamento).ler(Path(caminho))
    for erro in erros:
        log.debug("linha %s ignorada: %s", erro.linha, erro.mensagem)

    ofertas: list[Oferta] = []
    for produto in produtos:
        promocional = getattr(produto, "preco_promocional", None)
        preco = getattr(produto, "preco", None)
        if somente_promocao and promocional is None:
            continue
        imagens = getattr(produto, "imagens", []) or []
        url = getattr(imagens[0], "url", None) if imagens else None
        ofertas.append(Oferta(
            nome=produto.nome,
            preco_de=moeda(preco) if promocional is not None else None,
            preco_por=moeda(promocional if promocional is not None else preco),
            imagem=_resolver_imagem(url or "", Path(caminho).parent, 0),
            sku=produto.sku,
        ))
    if not ofertas:
        raise ValueError(
            f"{caminho}: nenhum produto com preço promocional. "
            f"Use --todos para incluir os produtos sem promoção."
        )
    return ofertas
