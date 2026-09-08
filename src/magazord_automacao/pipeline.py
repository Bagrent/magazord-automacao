"""Orchestration: spreadsheet in, products in Magazord, report out."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .client import MagazordClient, MagazordError
from .estado import Estado
from .imagens import ImagemInvalida, ServicoImagens
from .mapper import MapeadorPayload
from .models import ErroLinha, Produto, ResultadoEnvio

log = logging.getLogger(__name__)


@dataclass
class Opcoes:
    tamanho_lote: int = 50
    paralelismo: int = 4
    simular: bool = False        # dry run: build payloads, send nothing
    forcar: bool = False         # re-send even if unchanged since last run
    parar_no_erro: bool = False
    limite: int | None = None    # process at most N products (for a pilot run)
    enviar_imagens: bool = True


@dataclass
class Resumo:
    criados: int = 0
    atualizados: int = 0
    inalterados: int = 0
    simulados: int = 0
    erros: int = 0
    imagens: int = 0
    resultados: list[ResultadoEnvio] = field(default_factory=list)
    erros_leitura: list[ErroLinha] = field(default_factory=list)

    @property
    def total_processado(self) -> int:
        return self.criados + self.atualizados + self.inalterados + self.simulados + self.erros


def lotes(itens: Sequence[Produto], tamanho: int) -> Iterable[Sequence[Produto]]:
    for inicio in range(0, len(itens), max(1, tamanho)):
        yield itens[inicio:inicio + tamanho]


class Pipeline:
    def __init__(
        self,
        client: MagazordClient,
        mapeador: MapeadorPayload,
        imagens: ServicoImagens,
        estado: Estado,
        opcoes: Opcoes,
    ):
        self.client = client
        self.mapeador = mapeador
        self.imagens = imagens
        self.estado = estado
        self.opcoes = opcoes

    def executar(
        self, produtos: list[Produto], erros_leitura: list[ErroLinha] | None = None
    ) -> Resumo:
        resumo = Resumo(erros_leitura=list(erros_leitura or []))

        pendentes = self._filtrar(produtos, resumo)
        if self.opcoes.limite is not None:
            pendentes = pendentes[: self.opcoes.limite]

        log.info(
            "%d produtos na planilha, %d a enviar, %d inalterados",
            len(produtos), len(pendentes), resumo.inalterados,
        )

        for numero, lote in enumerate(lotes(pendentes, self.opcoes.tamanho_lote), start=1):
            log.info("lote %d: %d produtos", numero, len(lote))
            with ThreadPoolExecutor(max_workers=max(1, self.opcoes.paralelismo)) as pool:
                resultados = list(pool.map(self._processar, lote))

            for resultado in resultados:
                self._contabilizar(resumo, resultado)

            if self.opcoes.parar_no_erro and any(r.status == "erro" for r in resultados):
                log.error("interrompendo: --parar-no-erro está ativo e o lote %d teve falhas", numero)
                break

        return resumo

    # -- steps -------------------------------------------------------------

    def _filtrar(self, produtos: list[Produto], resumo: Resumo) -> list[Produto]:
        """Drop products whose content hash matches the last successful upload."""
        if self.opcoes.forcar:
            return list(produtos)

        pendentes = []
        for produto in produtos:
            if self.estado.inalterado(produto.sku, produto.hash_conteudo()):
                resumo.inalterados += 1
                resumo.resultados.append(ResultadoEnvio(
                    linha=produto.linha, sku=produto.sku, nome=produto.nome,
                    status="inalterado", id_magazord=self.estado.id_conhecido(produto.sku),
                    mensagem="sem alterações desde o último envio",
                ))
            else:
                pendentes.append(produto)
        return pendentes

    def _processar(self, produto: Produto) -> ResultadoEnvio:
        base = {"linha": produto.linha, "sku": produto.sku, "nome": produto.nome}
        try:
            payload = self.mapeador.payload_produto(produto)

            problemas = (
                self.imagens.verificar_todas(produto.imagens)
                if self.opcoes.enviar_imagens else []
            )
            if problemas:
                return ResultadoEnvio(**base, status="erro",
                                      mensagem="imagem inválida: " + "; ".join(problemas))

            if self.opcoes.simular:
                previstas = len(produto.imagens) if self.opcoes.enviar_imagens else 0
                log.debug("[simulação] %s -> %s", produto.sku, payload)
                return ResultadoEnvio(
                    **base, status="simulado", imagens_enviadas=previstas,
                    mensagem=f"{len(payload)} campos, {previstas} imagens",
                )

            id_produto, status = self._criar_ou_atualizar(produto, payload)

            enviadas = 0
            if self.opcoes.enviar_imagens and id_produto:
                enviadas = self._enviar_imagens(id_produto, produto)

            self.estado.marcar(produto.sku, produto.hash_conteudo(), id_produto, enviadas)
            return ResultadoEnvio(**base, status=status, id_magazord=id_produto,
                                  imagens_enviadas=enviadas)

        except (MagazordError, ImagemInvalida) as exc:
            log.error("linha %s (%s): %s", produto.linha, produto.sku, exc)
            detalhe = getattr(exc, "corpo", "")
            return ResultadoEnvio(**base, status="erro",
                                  mensagem=f"{exc}{' | ' + detalhe if detalhe else ''}")
        except Exception as exc:                      # noqa: BLE001 - one bad row must not kill the run
            log.exception("erro inesperado na linha %s (%s)", produto.linha, produto.sku)
            return ResultadoEnvio(**base, status="erro", mensagem=f"erro inesperado: {exc}")

    def _criar_ou_atualizar(self, produto: Produto, payload: dict[str, Any]) -> tuple[str | None, str]:
        """Look the SKU up in Magazord before writing, so a product that already
        exists is updated rather than duplicated -- even if the local state file
        was lost or the product was created by someone else."""
        id_produto = self.estado.id_conhecido(produto.sku) or self.client.buscar_por_sku(produto.sku)

        if id_produto:
            self.client.atualizar_produto(id_produto, payload)
            return id_produto, "atualizado"

        return self.client.criar_produto(payload), "criado"

    def _enviar_imagens(self, id_produto: str, produto: Produto) -> int:
        enviadas = 0
        for imagem in produto.imagens:
            if self.imagens.modo == "upload":
                arquivo = self.imagens.baixar(imagem)
                self.client.enviar_imagem_arquivo(
                    id_produto, arquivo,
                    self.imagens.cfg.get("campo_arquivo", "arquivo"),
                    self.mapeador.campos_imagem_multipart(imagem),
                )
            else:
                self.client.enviar_imagem_url(id_produto, self.mapeador.payload_imagem(imagem))
            enviadas += 1
        return enviadas

    @staticmethod
    def _contabilizar(resumo: Resumo, resultado: ResultadoEnvio) -> None:
        resumo.resultados.append(resultado)
        resumo.imagens += resultado.imagens_enviadas
        contador = {
            "criado": "criados", "atualizado": "atualizados",
            "inalterado": "inalterados", "simulado": "simulados", "erro": "erros",
        }.get(resultado.status)
        if contador:
            setattr(resumo, contador, getattr(resumo, contador) + 1)
