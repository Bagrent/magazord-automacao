from __future__ import annotations

import httpx
import pytest
import respx

from magazord_automacao.client import EndpointNaoConfigurado, MagazordClient, MagazordError


def cliente(api_config, **over):
    api_config.setdefault("http", {})
    api_config["http"].update({"min_interval_seconds": 0, "backoff_base_seconds": 0, **over})
    return MagazordClient(api_config, "usuario", "token")


def test_envia_basic_auth(api_config, base):
    with respx.mock(assert_all_called=True) as mock:
        rota = mock.post(f"{base}/site/produto").mock(
            return_value=httpx.Response(201, json={"id": 10})
        )
        with cliente(api_config) as c:
            c.criar_produto({"codigo": "A1"})
        # "usuario:token" base64-encoded
        assert rota.calls[0].request.headers["authorization"] == "Basic dXN1YXJpbzp0b2tlbg=="


def test_criar_produto_devolve_o_id(api_config, base):
    with respx.mock() as mock:
        mock.post(f"{base}/site/produto").mock(return_value=httpx.Response(201, json={"id": 4321}))
        with cliente(api_config) as c:
            assert c.criar_produto({"codigo": "A1"}) == "4321"


def test_criar_produto_acha_o_id_dentro_de_data(api_config, base):
    with respx.mock() as mock:
        mock.post(f"{base}/site/produto").mock(
            return_value=httpx.Response(201, json={"data": {"id": 99}})
        )
        with cliente(api_config) as c:
            assert c.criar_produto({"codigo": "A1"}) == "99"


def test_buscar_por_sku_encontra(api_config, base):
    with respx.mock() as mock:
        mock.get(f"{base}/site/produto").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 7, "codigo": "A1"}]})
        )
        with cliente(api_config) as c:
            assert c.buscar_por_sku("A1") == "7"


def test_buscar_por_sku_sem_resultado(api_config, base):
    with respx.mock() as mock:
        mock.get(f"{base}/site/produto").mock(return_value=httpx.Response(200, json={"items": []}))
        with cliente(api_config) as c:
            assert c.buscar_por_sku("A1") is None


def test_buscar_usa_o_parametro_configurado(api_config, base):
    api_config["endpoints"]["buscar_produto"]["sku_query_param"] = "referencia"
    with respx.mock() as mock:
        rota = mock.get(f"{base}/site/produto").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        with cliente(api_config) as c:
            c.buscar_por_sku("A1")
        assert rota.calls[0].request.url.params["referencia"] == "A1"


def test_tenta_de_novo_apos_429(api_config, base):
    with respx.mock() as mock:
        rota = mock.post(f"{base}/site/produto").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(201, json={"id": 1}),
            ]
        )
        with cliente(api_config) as c:
            assert c.criar_produto({"codigo": "A1"}) == "1"
        assert rota.call_count == 2


def test_tenta_de_novo_apos_erro_de_rede(api_config, base):
    with respx.mock() as mock:
        rota = mock.post(f"{base}/site/produto").mock(
            side_effect=[httpx.ConnectError("caiu"), httpx.Response(201, json={"id": 2})]
        )
        with cliente(api_config) as c:
            assert c.criar_produto({"codigo": "A1"}) == "2"
        assert rota.call_count == 2


def test_nao_tenta_de_novo_apos_400(api_config, base):
    with respx.mock() as mock:
        rota = mock.post(f"{base}/site/produto").mock(
            return_value=httpx.Response(400, text="EAN já cadastrado")
        )
        with cliente(api_config) as c, pytest.raises(MagazordError) as exc:
            c.criar_produto({"codigo": "A1"})
        assert rota.call_count == 1
        assert exc.value.status == 400
        assert "EAN já cadastrado" in exc.value.corpo


def test_desiste_depois_do_limite_de_tentativas(api_config, base):
    with respx.mock() as mock:
        rota = mock.post(f"{base}/site/produto").mock(return_value=httpx.Response(503))
        with cliente(api_config, max_retries=3) as c, pytest.raises(MagazordError):
            c.criar_produto({"codigo": "A1"})
        assert rota.call_count == 3


def test_endpoint_nao_configurado_da_erro_claro(api_config, base):
    api_config["endpoints"].pop("criar_imagem")
    with cliente(api_config) as c, pytest.raises(EndpointNaoConfigurado, match="criar_imagem"):
        c.enviar_imagem_url("1", {"url": "https://x/1.jpg"})


def test_atualizar_produto_monta_a_url_com_o_id(api_config, base):
    with respx.mock() as mock:
        rota = mock.put(f"{base}/site/produto/55").mock(return_value=httpx.Response(200, json={}))
        with cliente(api_config) as c:
            c.atualizar_produto("55", {"nome": "X"})
        assert rota.called
