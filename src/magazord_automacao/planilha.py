"""Reading products out of an Excel workbook.

The reader is deliberately tolerant: column headers are matched by alias
(accent- and case-insensitive), image columns are discovered by pattern rather
than by fixed position, and a bad cell fails only its own row instead of
aborting the run. Rows that fail come back as ``ErroLinha`` so the operator
gets one report listing everything wrong, not one error at a time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import ValidationError

from .models import ErroLinha, Imagem, Produto
from .texto import normalizar, normalizar_cabecalho
from .valores import ValorInvalido, booleano, decimal, digitos, inteiro, texto, vazio

# Columns parsed as money/dimensions, integers, barcodes and booleans.
_DECIMAIS = {"preco", "preco_promocional", "custo", "peso",
             "altura", "largura", "comprimento"}
_INTEIROS = {"estoque"}
_DIGITOS = {"ean", "ncm"}
_BOOLEANOS = {"ativo"}

_SUFIXO_NUMERO = re.compile(r"^(?P<prefixo>.*?)\s*(?P<numero>\d+)$")


class PlanilhaInvalida(RuntimeError):
    pass


class LeitorPlanilha:
    def __init__(self, mapeamento: dict[str, Any]):
        self.cfg = mapeamento
        self.planilha_cfg = mapeamento.get("planilha") or {}
        self.imagens_cfg = mapeamento.get("imagens") or {}
        self.numeros_cfg = mapeamento.get("numeros") or {}
        self.formato_numero = self.numeros_cfg.get("formato", "br")

        # alias -> canonical field name
        self.alias: dict[str, str] = {}
        for campo, aliases in (mapeamento.get("colunas") or {}).items():
            for a in aliases or []:
                self.alias[normalizar(a)] = campo
            self.alias.setdefault(normalizar(campo), campo)

        bool_cfg = mapeamento.get("booleanos") or {}
        self.verdadeiro = {normalizar(v) for v in bool_cfg.get("verdadeiro", [])}
        self.falso = {normalizar(v) for v in bool_cfg.get("falso", [])}

        self.prefixos_img = {normalizar(p) for p in self.imagens_cfg.get("prefixos_numerados", [])}
        self.delimitadas_img = {normalizar(c) for c in self.imagens_cfg.get("colunas_delimitadas", [])}
        self.delimitadores = self.imagens_cfg.get("delimitadores") or ["\n", ";", "|", ","]

    # -- header handling ---------------------------------------------------

    def _classificar(self, cabecalho: object) -> tuple[str, Any]:
        """Return (kind, detail) for one header cell.

        kind is one of: campo, imagem_numerada, imagem_delimitada, extra, vazio
        """
        chave = normalizar_cabecalho(cabecalho)
        if not chave:
            return "vazio", None
        if chave in self.alias:
            return "campo", self.alias[chave]
        if chave in self.delimitadas_img:
            return "imagem_delimitada", None
        m = _SUFIXO_NUMERO.match(chave)
        if m and normalizar(m.group("prefixo")) in self.prefixos_img:
            return "imagem_numerada", int(m.group("numero"))
        if chave in self.prefixos_img:            # a bare "Imagem" column
            return "imagem_numerada", 1
        return "extra", str(cabecalho).strip()

    def _achar_cabecalho(self, linhas: list[tuple[Any, ...]]) -> int:
        """Index of the header row within the first rows of the sheet."""
        configurado = self.planilha_cfg.get("linha_cabecalho")
        if configurado:
            return int(configurado) - 1

        melhor, melhor_pontos = 0, 0
        for i, linha in enumerate(linhas[:10]):
            pontos = sum(
                1 for c in linha if self._classificar(c)[0] in {"campo", "imagem_numerada", "imagem_delimitada"}
            )
            if pontos > melhor_pontos:
                melhor, melhor_pontos = i, pontos
        if melhor_pontos < 2:
            raise PlanilhaInvalida(
                "não foi possível identificar a linha de cabeçalho: nenhuma das "
                "primeiras 10 linhas contém pelo menos duas colunas conhecidas. "
                "Confira config/mapeamento.yaml ou informe planilha.linha_cabecalho."
            )
        return melhor

    # -- reading -----------------------------------------------------------

    def ler(self, caminho: Path) -> tuple[list[Produto], list[ErroLinha]]:
        if not caminho.exists():
            raise PlanilhaInvalida(f"planilha não encontrada: {caminho}")

        wb = load_workbook(caminho, read_only=True, data_only=True)
        try:
            aba = self.planilha_cfg.get("aba")
            ws = wb[aba] if aba else wb[wb.sheetnames[0]]
            linhas = list(ws.iter_rows(values_only=True))
        finally:
            wb.close()

        if not linhas:
            raise PlanilhaInvalida(f"a planilha {caminho.name} está vazia")

        idx_cabecalho = self._achar_cabecalho(linhas)
        cabecalhos = [self._classificar(c) for c in linhas[idx_cabecalho]]

        if not any(k == "campo" and d == "sku" for k, d in cabecalhos):
            conhecidas = ", ".join(sorted({d for k, d in cabecalhos if k == "campo"})) or "nenhuma"
            raise PlanilhaInvalida(
                f"a planilha não tem uma coluna de SKU reconhecível "
                f"(colunas reconhecidas: {conhecidas}). "
                f"Adicione o nome real da coluna em colunas.sku no mapeamento.yaml."
            )

        produtos: list[Produto] = []
        erros: list[ErroLinha] = []
        vistos: dict[str, int] = {}

        for deslocamento, valores_linha in enumerate(linhas[idx_cabecalho + 1:]):
            numero = idx_cabecalho + 2 + deslocamento   # 1-based, as Excel shows it
            resultado = self._ler_linha(numero, cabecalhos, valores_linha, vistos)
            if resultado is None:
                continue
            if isinstance(resultado, ErroLinha):
                erros.append(resultado)
            else:
                produtos.append(resultado)
                vistos[resultado.sku] = numero

        return produtos, erros

    def _ler_linha(
        self,
        numero: int,
        cabecalhos: list[tuple[str, Any]],
        valores_linha: tuple[Any, ...],
        vistos: dict[str, int],
    ) -> Produto | ErroLinha | None:
        if all(vazio(v) for v in valores_linha):
            return None

        bruto: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        numeradas: list[tuple[int, str]] = []
        delimitadas: list[str] = []

        for (tipo, detalhe), celula in zip(cabecalhos, valores_linha):
            if tipo == "campo":
                bruto[detalhe] = celula
            elif tipo == "imagem_numerada":
                url = texto(celula)
                if url:
                    numeradas.append((detalhe, url))
            elif tipo == "imagem_delimitada":
                delimitadas.extend(self._quebrar_urls(celula))
            elif tipo == "extra":
                if not vazio(celula):
                    extras[detalhe] = texto(celula)

        sku = texto(bruto.get("sku"))
        if not sku:
            if self.planilha_cfg.get("ignorar_linhas_sem_sku", True):
                return None
            return ErroLinha(linha=numero, campo="sku", mensagem="linha sem SKU")

        if sku in vistos:
            return ErroLinha(
                linha=numero, sku=sku, campo="sku",
                mensagem=f"SKU duplicado; já aparece na linha {vistos[sku]}",
            )

        try:
            campos = self._converter(bruto)
        except ValorInvalido as exc:
            return ErroLinha(linha=numero, sku=sku, campo=getattr(exc, "campo", None),
                             mensagem=str(exc))

        campos["imagens"] = self._montar_imagens(numeradas, delimitadas)
        campos["atributos"] = extras
        campos["linha"] = numero

        try:
            return Produto(**campos)
        except ValidationError as exc:
            primeiro = exc.errors()[0]
            campo = ".".join(str(p) for p in primeiro["loc"]) or None
            return ErroLinha(linha=numero, sku=sku, campo=campo,
                             mensagem=primeiro["msg"].removeprefix("Value error, "))

    def _converter(self, bruto: dict[str, Any]) -> dict[str, Any]:
        saida: dict[str, Any] = {}
        for campo, celula in bruto.items():
            try:
                if campo in _DECIMAIS:
                    saida[campo] = decimal(celula, self.formato_numero)
                elif campo in _INTEIROS:
                    saida[campo] = inteiro(celula, self.formato_numero)
                elif campo in _DIGITOS:
                    saida[campo] = digitos(celula)
                elif campo in _BOOLEANOS:
                    valor = booleano(celula, self.verdadeiro, self.falso)
                    if valor is not None:
                        saida[campo] = valor
                else:
                    saida[campo] = texto(celula)
            except ValorInvalido as exc:
                erro = ValorInvalido(f"coluna '{campo}': {exc}")
                erro.campo = campo  # type: ignore[attr-defined]
                raise erro from exc
        return {k: v for k, v in saida.items() if v is not None}

    def _quebrar_urls(self, celula: object) -> list[str]:
        valor = texto(celula)
        if not valor:
            return []
        partes = [valor]
        for delim in self.delimitadores:
            partes = [p for parte in partes for p in parte.split(delim)]
        return [p.strip() for p in partes if p.strip()]

    def _montar_imagens(
        self, numeradas: list[tuple[int, str]], delimitadas: list[str]
    ) -> list[dict[str, Any]]:
        """Numbered columns first (in column order), then delimited ones.

        Duplicate URLs are dropped: the same photo listed twice would otherwise
        be uploaded twice and show up twice on the product page.
        """
        ordenadas = [url for _, url in sorted(numeradas, key=lambda p: p[0])]
        vistas: set[str] = set()
        imagens: list[dict[str, Any]] = []
        for url in ordenadas + delimitadas:
            if url in vistas:
                continue
            vistas.add(url)
            imagens.append({"url": url, "ordem": len(imagens), "principal": not imagens})
        return imagens
