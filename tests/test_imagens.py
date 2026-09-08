from __future__ import annotations

import httpx
import pytest
import respx

from magazord_automacao.imagens import ImagemInvalida, ServicoImagens
from magazord_automacao.models import Imagem


@pytest.fixture
def servico(tmp_path):
    s = ServicoImagens(
        {"validar_antes_de_enviar": True, "extensoes_aceitas": [".jpg", ".png"],
         "tamanho_maximo_mb": 1},
        tmp_path / "cache",
    )
    yield s
    s.fechar()


def test_url_ok(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/1.jpg").mock(
            return_value=httpx.Response(200, headers={"content-type": "image/jpeg"})
        )
        servico.verificar(Imagem(url="https://cdn/1.jpg"))


def test_url_quebrada_e_recusada(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/1.jpg").mock(return_value=httpx.Response(404))
        with pytest.raises(ImagemInvalida, match="404"):
            servico.verificar(Imagem(url="https://cdn/1.jpg"))


def test_extensao_nao_aceita(servico):
    with pytest.raises(ImagemInvalida, match="extensão"):
        servico.verificar(Imagem(url="https://cdn/arquivo.pdf"))


def test_pagina_html_no_lugar_de_imagem(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/1.jpg").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/html"})
        )
        with pytest.raises(ImagemInvalida, match="content-type"):
            servico.verificar(Imagem(url="https://cdn/1.jpg"))


def test_imagem_grande_demais(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/1.jpg").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "image/jpeg", "content-length": str(5 * 1024 * 1024)}
            )
        )
        with pytest.raises(ImagemInvalida, match="MB"):
            servico.verificar(Imagem(url="https://cdn/1.jpg"))


def test_servidor_que_recusa_head_cai_para_get(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/1.jpg").mock(return_value=httpx.Response(405))
        mock.get("https://cdn/1.jpg").mock(
            return_value=httpx.Response(206, headers={"content-type": "image/jpeg"})
        )
        servico.verificar(Imagem(url="https://cdn/1.jpg"))


def test_verificar_todas_junta_os_problemas(servico):
    with respx.mock() as mock:
        mock.head("https://cdn/ok.jpg").mock(
            return_value=httpx.Response(200, headers={"content-type": "image/jpeg"})
        )
        mock.head("https://cdn/ruim.jpg").mock(return_value=httpx.Response(404))
        problemas = servico.verificar_todas(
            [Imagem(url="https://cdn/ok.jpg"), Imagem(url="https://cdn/ruim.jpg")]
        )
    assert len(problemas) == 1 and "ruim.jpg" in problemas[0]


def test_download_grava_no_cache_e_reaproveita(servico):
    with respx.mock() as mock:
        rota = mock.get("https://cdn/1.jpg").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8\xff dados")
        )
        caminho = servico.baixar(Imagem(url="https://cdn/1.jpg"))
        assert caminho.exists() and caminho.read_bytes() == b"\xff\xd8\xff dados"
        servico.baixar(Imagem(url="https://cdn/1.jpg"))
        assert rota.call_count == 1          # served from cache the second time


def test_download_falho_nao_deixa_arquivo_truncado(servico):
    with respx.mock() as mock:
        mock.get("https://cdn/1.jpg").mock(return_value=httpx.Response(500))
        with pytest.raises(ImagemInvalida):
            servico.baixar(Imagem(url="https://cdn/1.jpg"))
    assert list(servico.cache.glob("*")) == []


def test_url_sem_http_e_recusada_no_modelo():
    with pytest.raises(ValueError, match="http"):
        Imagem(url="cdn/1.jpg")
