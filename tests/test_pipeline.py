from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from magazord_automacao.client import MagazordClient
from magazord_automacao.estado import Estado
from magazord_automacao.imagens import ServicoImagens
from magazord_automacao.mapper import MapeadorPayload
from magazord_automacao.models import Imagem, Produto
from magazord_automacao.pipeline import Opcoes, Pipeline


@pytest.fixture
def montar(api_config, tmp_path):
    """Build a Pipeline wired to the mocked API, with image checks disabled."""
    api_config["http"].update({"min_interval_seconds": 0, "backoff_base_seconds": 0})
    api_config["imagens"]["validar_antes_de_enviar"] = False

    abertos = []

    def _montar(**opcoes):
        client = MagazordClient(api_config, "u", "t")
        estado = Estado(tmp_path / "estado.sqlite3")
        servico = ServicoImagens(api_config["imagens"], tmp_path / "cache")
        abertos.extend([client, servico, estado])
        pipeline = Pipeline(client, MapeadorPayload(api_config), servico, estado,
                            Opcoes(paralelismo=1, **opcoes))
        return pipeline, estado

    yield _montar

    for recurso in abertos:
        recurso.fechar()


def produto(sku="A1", **kw):
    kw.setdefault("nome", "Camiseta")
    kw.setdefault("preco", Decimal("89.90"))
    kw.setdefault("imagens", [Imagem(url="https://cdn/1.jpg", ordem=0, principal=True)])
    return Produto(sku=sku, linha=2, **kw)


def rotas(mock, base, *, existente=None, id_novo=1):
    mock.get(f"{base}/site/produto").mock(
        return_value=httpx.Response(200, json={"items": [{"id": existente}] if existente else []})
    )
    criar = mock.post(f"{base}/site/produto").mock(
        return_value=httpx.Response(201, json={"id": id_novo})
    )
    atualizar = mock.put(url__regex=rf"{base}/site/produto/\d+$").mock(
        return_value=httpx.Response(200, json={})
    )
    imagem = mock.post(url__regex=rf"{base}/site/produto/\d+/imagem$").mock(
        return_value=httpx.Response(201, json={})
    )
    return criar, atualizar, imagem


def test_produto_novo_e_criado_com_imagem(montar, base):
    pipeline, estado = montar()
    with respx.mock(assert_all_called=False) as mock:
        criar, atualizar, imagem = rotas(mock, base, id_novo=77)
        resumo = pipeline.executar([produto()])

    assert resumo.criados == 1 and resumo.atualizados == 0 and resumo.erros == 0
    assert criar.called and not atualizar.called
    assert imagem.call_count == 1
    assert resumo.resultados[0].id_magazord == "77"
    assert estado.id_conhecido("A1") == "77"


def test_sku_ja_existente_e_atualizado_nao_duplicado(montar, base):
    pipeline, estado = montar()
    with respx.mock(assert_all_called=False) as mock:
        criar, atualizar, _ = rotas(mock, base, existente=42)
        resumo = pipeline.executar([produto()])

    assert resumo.atualizados == 1 and resumo.criados == 0
    assert atualizar.called and not criar.called


def test_segunda_rodada_pula_o_que_nao_mudou(montar, base):
    pipeline, estado = montar()
    with respx.mock(assert_all_called=False) as mock:
        criar, _, _ = rotas(mock, base)
        pipeline.executar([produto()])
        resumo = pipeline.executar([produto()])

    assert resumo.inalterados == 1 and resumo.criados == 0
    assert criar.call_count == 1          # nothing re-sent


def test_preco_alterado_dispara_novo_envio(montar, base):
    pipeline, estado = montar()
    with respx.mock(assert_all_called=False) as mock:
        rotas(mock, base, id_novo=5)
        pipeline.executar([produto()])
        resumo = pipeline.executar([produto(preco=Decimal("99.90"))])

    assert resumo.atualizados == 1 and resumo.inalterados == 0


def test_forcar_reenvia_mesmo_sem_alteracao(montar, base):
    pipeline, _ = montar()
    with respx.mock(assert_all_called=False) as mock:
        rotas(mock, base, id_novo=5)
        pipeline.executar([produto()])

    pipeline_forcado, _ = montar(forcar=True)
    with respx.mock(assert_all_called=False) as mock:
        rotas(mock, base, id_novo=5)
        resumo = pipeline_forcado.executar([produto()])

    assert resumo.inalterados == 0


def test_um_produto_com_erro_nao_derruba_os_outros(montar, base):
    pipeline, _ = montar()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/site/produto").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        mock.post(f"{base}/site/produto").mock(
            side_effect=[
                httpx.Response(201, json={"id": 1}),
                httpx.Response(422, text="nome obrigatório"),
                httpx.Response(201, json={"id": 3}),
            ]
        )
        mock.post(url__regex=rf"{base}/site/produto/\d+/imagem$").mock(
            return_value=httpx.Response(201, json={})
        )
        resumo = pipeline.executar([produto("A1"), produto("A2"), produto("A3")])

    assert resumo.criados == 2 and resumo.erros == 1
    erro = next(r for r in resumo.resultados if r.status == "erro")
    assert erro.sku == "A2" and "nome obrigatório" in erro.mensagem


def test_produto_com_erro_nao_entra_no_estado(montar, base):
    pipeline, estado = montar()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/site/produto").mock(return_value=httpx.Response(200, json={"items": []}))
        mock.post(f"{base}/site/produto").mock(return_value=httpx.Response(500))
        pipeline.executar([produto("A9")])

    # A failed product must be retried on the next run, not marked as done.
    assert estado.registro("A9") is None


def test_simulacao_nao_envia_nada(montar, base):
    pipeline, estado = montar(simular=True)
    with respx.mock(assert_all_called=False) as mock:
        criar, atualizar, imagem = rotas(mock, base)
        resumo = pipeline.executar([produto()])

    assert resumo.simulados == 1
    assert not criar.called and not atualizar.called and not imagem.called
    assert estado.registro("A1") is None


def test_limite_processa_apenas_o_piloto(montar, base):
    pipeline, _ = montar(limite=2)
    with respx.mock(assert_all_called=False) as mock:
        criar, _, _ = rotas(mock, base)
        resumo = pipeline.executar([produto(f"A{i}") for i in range(5)])

    assert resumo.total_processado == 2 and criar.call_count == 2


def test_lotes_cobrem_todos_os_produtos(montar, base):
    pipeline, _ = montar(tamanho_lote=2)
    with respx.mock(assert_all_called=False) as mock:
        criar, _, _ = rotas(mock, base)
        resumo = pipeline.executar([produto(f"A{i}") for i in range(5)])

    assert resumo.criados == 5 and criar.call_count == 5


def test_sem_imagens_nao_chama_o_endpoint_de_imagem(montar, base):
    pipeline, _ = montar(enviar_imagens=False)
    with respx.mock(assert_all_called=False) as mock:
        _, _, imagem = rotas(mock, base)
        pipeline.executar([produto()])

    assert not imagem.called


def test_payload_usa_os_nomes_de_campo_configurados(api_config):
    payload = MapeadorPayload(api_config).payload_produto(
        produto(ean="7891234567895", estoque=10)
    )
    assert payload["codigo"] == "A1"
    assert payload["precoVenda"] == "89.90"      # string, not float
    assert payload["codigoBarras"] == "7891234567895"
    assert payload["estoque"] == 10


def test_campo_vazio_nao_vai_no_payload(api_config):
    payload = MapeadorPayload(api_config).payload_produto(produto(descricao=None))
    assert "descricao" not in payload


def test_campo_desativado_no_config_some_do_payload(api_config):
    api_config["campos"]["custo"] = None
    payload = MapeadorPayload(api_config).payload_produto(produto(custo=Decimal("10")))
    assert "precoCusto" not in payload


def test_chave_com_ponto_vira_objeto_aninhado(api_config):
    api_config["campos"]["preco"] = "preco.venda"
    payload = MapeadorPayload(api_config).payload_produto(produto())
    assert payload["preco"] == {"venda": "89.90"}
