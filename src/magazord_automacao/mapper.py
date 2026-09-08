"""Translating a canonical Produto into the Magazord JSON payload.

The whole translation is driven by the ``campos`` table in
config/magazord.yaml, so renaming a field to match the real API spec never
touches Python.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import Imagem, Produto


def _atribuir(destino: dict[str, Any], caminho: str, valor: Any) -> None:
    """Set a dotted path, creating intermediate dicts. 'a.b' -> {'a': {'b': v}}."""
    partes = caminho.split(".")
    atual = destino
    for parte in partes[:-1]:
        proximo = atual.get(parte)
        if not isinstance(proximo, dict):
            proximo = {}
            atual[parte] = proximo
        atual = proximo
    atual[partes[-1]] = valor


def _serializar(valor: Any) -> Any:
    # Decimal is not JSON-serialisable, and float() on money invites rounding
    # surprises, so prices go out as strings like "89.90".
    if isinstance(valor, Decimal):
        return format(valor, "f")
    return valor


class MapeadorPayload:
    def __init__(self, config: dict[str, Any]):
        self.campos: dict[str, str | None] = config.get("campos") or {}
        self.extras_cfg = config.get("extras") or {}
        self.imagens_cfg = config.get("imagens") or {}

    def payload_produto(self, produto: Produto) -> dict[str, Any]:
        """Build the product body. Fields that are None are omitted entirely,
        so a blank spreadsheet cell never overwrites a value already set in the
        store with an empty one."""
        payload: dict[str, Any] = {}

        for campo_canonico, chave_api in self.campos.items():
            if not chave_api:                        # explicitly disabled in config
                continue
            valor = getattr(produto, campo_canonico, None)
            if valor is None:
                continue
            _atribuir(payload, chave_api, _serializar(valor))

        self._aplicar_extras(payload, produto)
        return payload

    def _aplicar_extras(self, payload: dict[str, Any], produto: Produto) -> None:
        modo = self.extras_cfg.get("modo", "ignorar")
        if modo == "ignorar" or not produto.atributos:
            return

        if modo == "raiz":
            for chave, valor in produto.atributos.items():
                payload.setdefault(chave, _serializar(valor))
            return

        if modo == "atributo":
            nome_key = self.extras_cfg.get("atributo_nome_key", "nome")
            valor_key = self.extras_cfg.get("atributo_valor_key", "valor")
            payload[self.extras_cfg.get("atributos_payload_key", "atributos")] = [
                {nome_key: chave, valor_key: _serializar(valor)}
                for chave, valor in produto.atributos.items()
            ]

    def payload_imagem(self, imagem: Imagem) -> dict[str, Any]:
        return {
            self.imagens_cfg.get("campo_url", "url"): imagem.url,
            self.imagens_cfg.get("campo_ordem", "ordem"): imagem.ordem,
            self.imagens_cfg.get("campo_principal", "principal"): imagem.principal,
        }

    def campos_imagem_multipart(self, imagem: Imagem) -> dict[str, Any]:
        """The non-file fields that accompany a multipart image upload."""
        return {
            self.imagens_cfg.get("campo_ordem", "ordem"): imagem.ordem,
            self.imagens_cfg.get("campo_principal", "principal"): imagem.principal,
        }
