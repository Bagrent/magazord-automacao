"""The canonical product model.

This is the pipeline's own vocabulary. The spreadsheet reader produces these,
and ``mapper.py`` translates them into whatever shape the Magazord API wants,
so a change to either side never ripples across the whole codebase.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .valores import ean_valido


class Imagem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    ordem: int = 0
    principal: bool = False

    @field_validator("url")
    @classmethod
    def _url_http(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError(f"URL de imagem precisa começar com http:// ou https:// ({v!r})")
        return v


class Produto(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sku: str
    nome: str
    descricao: str | None = None
    descricao_curta: str | None = None
    ean: str | None = None
    marca: str | None = None
    categoria: str | None = None

    preco: Decimal | None = None
    preco_promocional: Decimal | None = None
    custo: Decimal | None = None
    estoque: int | None = None

    peso: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None

    ncm: str | None = None
    ativo: bool = True

    sku_pai: str | None = None
    variacao_cor: str | None = None
    variacao_tamanho: str | None = None

    imagens: list[Imagem] = Field(default_factory=list)
    atributos: dict[str, Any] = Field(default_factory=dict)

    # Provenance, for error reporting. Excluded from the content hash.
    linha: int = 0

    @field_validator("sku", "nome")
    @classmethod
    def _obrigatorio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("campo obrigatório está vazio")
        return v.strip()

    @field_validator("ean")
    @classmethod
    def _ean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not ean_valido(v):
            raise ValueError(f"EAN/GTIN inválido (dígito verificador não confere): {v}")
        return v

    @field_validator("preco", "preco_promocional", "custo", "peso",
                     "altura", "largura", "comprimento")
    @classmethod
    def _nao_negativo(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError(f"valor não pode ser negativo: {v}")
        return v

    @field_validator("estoque")
    @classmethod
    def _estoque_nao_negativo(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"estoque não pode ser negativo: {v}")
        return v

    def hash_conteudo(self) -> str:
        """A stable fingerprint of everything that would be sent to Magazord.

        The pipeline stores this per SKU so a re-run can skip products that
        haven't changed since the last successful upload. ``linha`` is excluded
        because inserting a row above a product must not force a re-upload.
        """
        corpo = self.model_dump(mode="json", exclude={"linha"})
        canonico = json.dumps(corpo, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


class ErroLinha(BaseModel):
    """A row that could not be turned into a valid Produto."""

    linha: int
    sku: str | None = None
    campo: str | None = None
    mensagem: str


class ResultadoEnvio(BaseModel):
    """The outcome of one product, for the run report."""

    linha: int
    sku: str
    nome: str = ""
    # criado | atualizado | inalterado | erro | simulado
    status: str
    id_magazord: str | None = None
    imagens_enviadas: int = 0
    mensagem: str = ""
