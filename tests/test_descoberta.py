from __future__ import annotations

import httpx
import respx

from magazord_automacao.descoberta import (
    Descobridor,
    _achatar,
    _encontrar_lista,
    formatar_relatorio,
)

RAIZ = "https://loja.teste.local/api"


def descobridor() -> Descobridor:
    return Descobridor(RAIZ, "usuario", "token")


# -- flattening -------------------------------------------------------------

def test_achatar_usa_notacao_pontuada_do_yaml():
    campos = _achatar({"codigo": "A1", "preco": {"venda": 89.9, "custo": 40.0}})
    assert campos["codigo"] == "A1"
    assert campos["preco.venda"] == "89.9"
    assert campos["preco.custo"] == "40.0"


def test_achatar_entra_no_primeiro_item_de_uma_lista_de_objetos():
    campos = _achatar({"imagens": [{"url": "http://x/1.jpg", "ordem": 1}]})
    assert campos["imagens[].url"] == "http://x/1.jpg"
    assert campos["imagens[].ordem"] == "1"


def test_achatar_resume_lista_de_escalares():
    campos = _achatar({"tags": ["a", "b", "c"]})
    assert campos["tags"].startswith("lista(3)")


def test_achatar_trunca_valor_muito_longo():
    campos = _achatar({"descricao": "x" * 200})
    assert len(campos["descricao"]) == 60
    assert campos["descricao"].endswith("...")


# -- locating the record list ----------------------------------------------

def test_encontrar_lista_em_corpo_que_ja_e_array():
    caminho, registros = _encontrar_lista([{"id": 1}])
    assert caminho == ""
    assert len(registros) == 1


def test_encontrar_lista_dentro_de_items():
    caminho, registros = _encontrar_lista({"items": [{"id": 1}]})
    assert caminho == "items"


def test_encontrar_lista_aninhada_em_data_items():
    caminho, registros = _encontrar_lista({"data": {"items": [{"id": 1}]}})
    assert caminho == "data.items"
    assert len(registros) == 1


def test_encontrar_lista_desiste_sem_inventar():
    caminho, registros = _encontrar_lista({"mensagem": "nada aqui"})
    assert caminho == ""
    assert registros == []


# -- probing ----------------------------------------------------------------

def test_encontra_o_caminho_que_responde_e_para_de_sondar():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7, "codigo": "A1"}]})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7").mock(
            return_value=httpx.Response(200, json={"id": 7, "codigo": "A1", "nome": "Shampoo"})
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            r = d.executar()

    assert r.autenticou is True
    assert r.caminho_produto == "/v2/site/produto"
    assert r.caminho_resultados == "items"
    assert r.campo_id == "id"


def test_le_o_produto_individual_para_ver_todos_os_campos():
    """The list view is trimmed; the detail view is the real contract."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7, "codigo": "A1"}]})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7").mock(
            return_value=httpx.Response(
                200,
                json={"id": 7, "codigo": "A1", "nome": "Shampoo", "codigoBarras": "789"},
            )
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            r = d.executar()

    # "nome" and "codigoBarras" exist only in the detail view.
    assert "nome" in r.campos_descobertos
    assert r.campos_descobertos["codigoBarras"] == "789"


def test_desembrulha_registro_unico_dentro_de_data():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7}]})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7").mock(
            return_value=httpx.Response(200, json={"data": {"id": 7, "nome": "Creme"}, "success": True})
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            r = d.executar()

    assert r.campos_descobertos["nome"] == "Creme"
    assert "data.nome" not in r.campos_descobertos


def test_credencial_recusada_nao_e_confundida_com_caminho_inexistente():
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(401, text="unauthorized"))
        with descobridor() as d:
            r = d.executar()

    assert r.autenticou is False
    assert "401" in r.detalhe_auth
    assert any(s.protegido for s in r.sondas)


def test_envia_basic_auth_em_toda_sonda():
    with respx.mock(assert_all_called=False) as mock:
        rota = mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            d.executar()
    # "usuario:token" base64-encoded
    assert rota.calls[0].request.headers["authorization"] == "Basic dXN1YXJpbzp0b2tlbg=="


def test_a_descoberta_nunca_escreve():
    """Discovery runs against production stores, so GET only -- never a write."""
    with respx.mock(assert_all_called=False) as mock:
        rota = mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            d.executar()
    assert {c.request.method for c in rota.calls} == {"GET"}


def test_erro_de_conexao_vira_sonda_com_erro_em_vez_de_explodir():
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(side_effect=httpx.ConnectError("sem rota para o host"))
        with descobridor() as d:
            r = d.executar()

    assert r.autenticou is False
    assert any(s.erro for s in r.sondas)


def test_loja_sem_nenhum_produto_cadastrado():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            r = d.executar()

    assert r.autenticou is True
    assert r.produto_exemplo is None
    assert "cadastre UM pelo painel" in formatar_relatorio(r)


# -- reporting --------------------------------------------------------------

def test_relatorio_traz_o_yaml_pronto_para_colar():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7, "codigo": "A1"}]})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7").mock(
            return_value=httpx.Response(200, json={"id": 7, "codigo": "A1"})
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            texto = formatar_relatorio(d.executar())

    assert 'path: "/v2/site/produto"' in texto
    assert 'results_path: "items"' in texto
    assert 'path: "/v2/site/produto/{id}"' in texto


def test_relatorio_de_falha_explica_o_que_fazer():
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(return_value=httpx.Response(401))
        with descobridor() as d:
            texto = formatar_relatorio(d.executar())

    assert "AUTENTICAÇÃO: não confirmada" in texto
    assert "Configurações > API" in texto


def test_caminhos_de_apoio_voltam_com_o_marcador_id_e_nao_o_id_concreto():
    """Probing needs a real id; the config needs the {id} template."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{RAIZ}/v2/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7}]})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7").mock(
            return_value=httpx.Response(200, json={"id": 7, "codigo": "A1"})
        )
        mock.get(f"{RAIZ}/v2/site/produto/7/imagem").mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.route().mock(return_value=httpx.Response(404))
        with descobridor() as d:
            r = d.executar()

    assert r.apoio["imagem"] == "/v2/site/produto/{id}/imagem"
    assert 'path: "/v2/site/produto/{id}/imagem"' in formatar_relatorio(r)
