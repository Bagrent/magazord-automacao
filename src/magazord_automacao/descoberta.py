"""Discovers the real Magazord API contract from a live store.

The endpoint paths and payload field names in ``config/magazord.yaml`` started
life as educated guesses, because the official spec host is not reachable from
every machine. This module replaces guessing with evidence: it talks to the
store the credentials belong to, finds which paths actually answer, reads a
product that is already registered, and reports the field names Magazord itself
uses.

Everything here is **read-only**. Only GET is ever sent, so running a discovery
against a production store cannot create, change or delete anything.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Path candidates, relative to the base URL. Ordered most to least likely.
# The base URL may or may not already carry the version, so both shapes are
# tried and whichever answers wins.
CAMINHOS_PRODUTO = [
    "/v2/site/produto",
    "/v2/produto",
    "/site/produto",
    "/produto",
    "/v1/site/produto",
    "/v1/produto",
]

# Looked up once a product path is known, to find the supporting endpoints.
CAMINHOS_APOIO = {
    "marca": ["{base}/marca", "{v}/site/marca", "{v}/marca"],
    "categoria": ["{base}/categoria", "{v}/site/categoria", "{v}/categoria"],
    "imagem": ["{produto}/imagem", "{produto}-imagem", "{base}/produto-imagem"],
    "estoque": ["{produto}/estoque", "{base}/produto-estoque", "{v}/site/estoque"],
    "preco": ["{produto}/preco", "{base}/produto-preco", "{v}/site/preco"],
}

# Query parameters Magazord installations use for paging, tried in order.
PARAMS_PAGINA = [{"limit": 1}, {"limite": 1}, {"pageSize": 1}, {}]


@dataclass
class Sonda:
    """The outcome of probing one path."""

    caminho: str
    url: str
    status: int | None = None
    erro: str = ""
    tipo_conteudo: str = ""
    amostra: Any = None

    @property
    def existe(self) -> bool:
        """A path that authenticated and returned data."""
        return self.status == 200

    @property
    def protegido(self) -> bool:
        """The path is real but the credentials were rejected for it."""
        return self.status in (401, 403)

    @property
    def ausente(self) -> bool:
        return self.status in (404, 405)

    def resumo(self) -> str:
        if self.erro:
            return f"{self.caminho:<28} ERRO  {self.erro}"
        marca = {True: "OK   "}.get(self.existe, "     ")
        if self.protegido:
            marca = "AUTH "
        elif self.ausente:
            marca = "--   "
        return f"{self.caminho:<28} {marca} HTTP {self.status}"


@dataclass
class Resultado:
    base_url: str
    versao_removida: str = ""
    autenticou: bool = False
    detalhe_auth: str = ""
    sondas: list[Sonda] = field(default_factory=list)
    caminho_produto: str = ""
    param_pagina: dict[str, Any] = field(default_factory=dict)
    caminho_resultados: str = ""
    campo_id: str = ""
    produto_exemplo: dict[str, Any] | None = None
    campos_descobertos: dict[str, str] = field(default_factory=dict)
    apoio: dict[str, str] = field(default_factory=dict)

    def para_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "versao_removida": self.versao_removida,
            "autenticou": self.autenticou,
            "detalhe_auth": self.detalhe_auth,
            "caminho_produto": self.caminho_produto,
            "param_pagina": self.param_pagina,
            "caminho_resultados": self.caminho_resultados,
            "campo_id": self.campo_id,
            "campos_descobertos": self.campos_descobertos,
            "endpoints_apoio": self.apoio,
            "produto_exemplo": self.produto_exemplo,
            "sondas": [
                {"caminho": s.caminho, "status": s.status, "erro": s.erro}
                for s in self.sondas
            ],
        }


def _normalizar_base(base_url: str) -> tuple[str, str]:
    """Strip a trailing version segment from the base URL.

    The version belongs to the candidate paths, not the base: a base of
    ``.../api/v2`` plus a candidate of ``/v2/site/produto`` would probe
    ``/api/v2/v2/site/produto`` and find nothing. Both ``.../api`` and
    ``.../api/v2`` are things people reasonably paste, so accept either.
    """
    limpa = base_url.strip().rstrip("/")
    ultimo = limpa.rsplit("/", 1)[-1]
    if len(ultimo) == 2 and ultimo[0] == "v" and ultimo[1].isdigit():
        return limpa[: -(len(ultimo) + 1)], ultimo
    return limpa, ""


def _achatar(dados: Any, prefixo: str = "") -> dict[str, str]:
    """Flatten a product object into ``dotted.key -> sample value`` pairs.

    Nested objects become dotted keys, which is exactly the notation
    ``config/magazord.yaml`` uses under ``campos``, so the output can be copied
    straight into it.
    """
    achatado: dict[str, str] = {}
    if isinstance(dados, dict):
        for chave, valor in dados.items():
            caminho = f"{prefixo}.{chave}" if prefixo else str(chave)
            if isinstance(valor, dict):
                achatado.update(_achatar(valor, caminho))
            elif isinstance(valor, list):
                amostra = valor[0] if valor else None
                if isinstance(amostra, dict):
                    achatado.update(_achatar(amostra, f"{caminho}[]"))
                else:
                    achatado[caminho] = f"lista({len(valor)}) ex: {amostra!r}"
            else:
                texto = str(valor)
                if len(texto) > 60:
                    texto = texto[:57] + "..."
                achatado[caminho] = texto
    return achatado


def _encontrar_lista(corpo: Any) -> tuple[str, list[Any]]:
    """Find the list of records in a response body, and the dotted path to it.

    Magazord installations wrap results differently (``items``, ``data``,
    ``data.items``…), and some return a bare array. Rather than assume, look.
    """
    if isinstance(corpo, list):
        return "", corpo
    if not isinstance(corpo, dict):
        return "", []
    for chave in ("items", "data", "results", "registros", "content", "list"):
        valor = corpo.get(chave)
        if isinstance(valor, list):
            return chave, valor
        if isinstance(valor, dict):
            for interna in ("items", "data", "results", "registros"):
                if isinstance(valor.get(interna), list):
                    return f"{chave}.{interna}", valor[interna]
    return "", []


def _adivinhar_campo_id(registro: dict[str, Any]) -> str:
    for chave in ("id", "idProduto", "produtoId", "codigo", "sku"):
        if chave in registro:
            return chave
    return ""


class Descobridor:
    """Probes a live store and reports what its API actually looks like."""

    def __init__(
        self,
        base_url: str,
        usuario: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url, self.versao_removida = _normalizar_base(base_url)
        self.usuario = usuario
        self.token = token
        credencial = base64.b64encode(f"{usuario}:{token}".encode()).decode()
        self._cliente = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Basic {credencial}",
                "Accept": "application/json",
                "User-Agent": "magazord-automacao/descoberta",
            },
            follow_redirects=True,
        )

    def __enter__(self) -> "Descobridor":
        return self

    def __exit__(self, *_: object) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._cliente.close()

    # -- probing ------------------------------------------------------------

    def _sondar(self, caminho: str, params: dict[str, Any] | None = None) -> Sonda:
        url = f"{self.base_url}{caminho}"
        sonda = Sonda(caminho=caminho, url=url)
        try:
            resposta = self._cliente.get(url, params=params or {})
        except httpx.HTTPError as exc:
            sonda.erro = f"{type(exc).__name__}: {exc}"
            log.debug("sonda %s falhou: %s", caminho, exc)
            return sonda
        sonda.status = resposta.status_code
        sonda.tipo_conteudo = resposta.headers.get("content-type", "")
        if resposta.status_code == 200 and "json" in sonda.tipo_conteudo:
            try:
                sonda.amostra = resposta.json()
            except ValueError:
                sonda.erro = "resposta 200 não era JSON válido"
        elif resposta.status_code >= 400:
            corpo = resposta.text[:200].replace("\n", " ")
            if corpo:
                sonda.erro = corpo
        log.debug("sonda %s -> %s", caminho, resposta.status_code)
        return sonda

    def executar(self) -> Resultado:
        """Run the full read-only discovery and return what was found."""
        resultado = Resultado(
            base_url=self.base_url, versao_removida=self.versao_removida
        )

        # 1. Find a product path that answers, and the paging param it wants.
        for caminho in CAMINHOS_PRODUTO:
            for params in PARAMS_PAGINA:
                sonda = self._sondar(caminho, params)
                resultado.sondas.append(sonda)
                if sonda.existe:
                    resultado.caminho_produto = caminho
                    resultado.param_pagina = params
                    resultado.autenticou = True
                    resultado.detalhe_auth = "Basic Auth aceito"
                    break
                if sonda.protegido:
                    resultado.detalhe_auth = (
                        f"HTTP {sonda.status} em {caminho} — o caminho existe, "
                        "mas as credenciais foram recusadas para ele"
                    )
                    # A 401 says the path is real; no point retrying paging
                    # variants against it.
                    break
                if sonda.ausente:
                    break
            if resultado.caminho_produto:
                break

        if not resultado.caminho_produto:
            return resultado

        # 2. Read one real product: its keys are the payload contract.
        sonda = next(s for s in resultado.sondas if s.caminho == resultado.caminho_produto
                     and s.existe)
        caminho_lista, registros = _encontrar_lista(sonda.amostra)
        resultado.caminho_resultados = caminho_lista
        if registros and isinstance(registros[0], dict):
            exemplo = registros[0]
            resultado.campo_id = _adivinhar_campo_id(exemplo)
            # The list view is often trimmed; fetch the record on its own to
            # see every field the create payload may carry.
            if resultado.campo_id and exemplo.get(resultado.campo_id) is not None:
                detalhe = self._sondar(
                    f"{resultado.caminho_produto}/{exemplo[resultado.campo_id]}"
                )
                resultado.sondas.append(detalhe)
                if detalhe.existe and isinstance(detalhe.amostra, dict):
                    corpo = detalhe.amostra
                    # Some installations wrap the single record in "data".
                    if set(corpo) <= {"data", "success", "meta"} and isinstance(
                        corpo.get("data"), dict
                    ):
                        corpo = corpo["data"]
                    exemplo = corpo
            resultado.produto_exemplo = exemplo
            resultado.campos_descobertos = _achatar(exemplo)

        # 3. Locate the supporting endpoints around it.
        versao = resultado.caminho_produto.rsplit("/", 1)[0] or ""
        raiz_versao = "/" + resultado.caminho_produto.strip("/").split("/")[0] \
            if resultado.caminho_produto.strip("/").split("/")[0].startswith("v") else ""
        exemplo_id = ""
        if resultado.produto_exemplo and resultado.campo_id:
            exemplo_id = str(resultado.produto_exemplo.get(resultado.campo_id, ""))
        for nome, moldes in CAMINHOS_APOIO.items():
            for molde in moldes:
                caminho = (
                    molde.replace("{produto}", f"{resultado.caminho_produto}/{exemplo_id}"
                                  if exemplo_id else resultado.caminho_produto)
                    .replace("{base}", versao)
                    .replace("{v}", raiz_versao)
                )
                if not exemplo_id and "{produto}" in molde:
                    continue
                sonda = self._sondar(caminho)
                resultado.sondas.append(sonda)
                if sonda.existe:
                    # Probed with a concrete id, but the config wants the
                    # template, so put the placeholder back.
                    resultado.apoio[nome] = (
                        caminho.replace(f"/{exemplo_id}", "/{id}")
                        if exemplo_id
                        else caminho
                    )
                    break
        return resultado


# -- reporting --------------------------------------------------------------

def formatar_relatorio(r: Resultado) -> str:
    """A readable report that maps line by line onto config/magazord.yaml."""
    linhas: list[str] = []
    add = linhas.append

    add("=" * 72)
    add("DESCOBERTA DA API MAGAZORD")
    add("=" * 72)
    add(f"base_url: {r.base_url}")
    if r.versao_removida:
        add(f"  (o /{r.versao_removida} do fim foi removido — a versão entra no caminho, não na base)")

    if not r.autenticou:
        add("")
        add("AUTENTICAÇÃO: não confirmada.")
        add(f"  {r.detalhe_auth or 'nenhum caminho de produto respondeu 200'}")
        add("")
        add("Sondas enviadas:")
        for s in r.sondas:
            add(f"  {s.resumo()}")
        add("")
        add("O que fazer:")
        add("  - HTTP 401/403 em todos: usuário/token errados, ou o token de API")
        add("    ainda não foi gerado no painel (Configurações > API).")
        add("  - HTTP 404 em todos: a base_url está incompleta. Tente incluir")
        add("    ou remover o /api no final.")
        add("  - Erro de conexão: rede ou DNS, a API nem foi alcançada.")
        return "\n".join(linhas)

    add(f"autenticação: OK (HTTP Basic)")
    add("")
    add("-" * 72)
    add("ENDPOINTS ENCONTRADOS")
    add("-" * 72)
    add(f"  produto:   {r.caminho_produto}")
    for nome, caminho in r.apoio.items():
        add(f"  {nome + ':':<11}{caminho}")
    if r.param_pagina:
        add(f"  paginação: {r.param_pagina}")
    add(f"  lista em:  {r.caminho_resultados or '(o corpo já é a lista)'}")
    add(f"  id em:     {r.campo_id or '(não identificado)'}")

    add("")
    add("-" * 72)
    add(f"CAMPOS REAIS DE UM PRODUTO ({len(r.campos_descobertos)} encontrados)")
    add("-" * 72)
    if r.campos_descobertos:
        largura = min(max(len(c) for c in r.campos_descobertos), 34)
        for chave, valor in sorted(r.campos_descobertos.items()):
            add(f"  {chave:<{largura}}  = {valor}")
    else:
        add("  nenhum produto cadastrado ainda — cadastre UM pelo painel,")
        add("  manualmente, e rode a descoberta de novo. Esse produto vira o")
        add("  molde para todos os outros.")

    add("")
    add("-" * 72)
    add("O QUE COLOCAR EM config/magazord.yaml")
    add("-" * 72)
    add("endpoints:")
    add("  buscar_produto:")
    add(f"    path: \"{r.caminho_produto}\"")
    add(f"    results_path: \"{r.caminho_resultados}\"")
    add(f"    id_field: \"{r.campo_id}\"")
    add("  criar_produto:")
    add(f"    path: \"{r.caminho_produto}\"")
    add("  atualizar_produto:")
    add(f"    path: \"{r.caminho_produto}/{{id}}\"")
    if "imagem" in r.apoio:
        add("  criar_imagem:")
        add(f"    path: \"{r.apoio['imagem']}\"")
    add("")
    add("Confira em `campos:` que cada nome à direita aparece na lista de")
    add("CAMPOS REAIS acima. Um nome que não aparece lá é um campo que a")
    add("Magazord vai ignorar silenciosamente.")
    return "\n".join(linhas)


def salvar(r: Resultado, pasta: Path) -> Path:
    """Write the raw findings as JSON, for pasting into an issue or a chat."""
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = pasta / f"descoberta-{carimbo}.json"
    destino.write_text(
        json.dumps(r.para_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destino
