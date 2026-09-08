from __future__ import annotations

from decimal import Decimal

import pytest
from openpyxl import Workbook

from magazord_automacao.planilha import LeitorPlanilha, PlanilhaInvalida


def escrever(tmp_path, linhas, nome="p.xlsx"):
    wb = Workbook()
    for linha in linhas:
        wb.active.append(linha)
    caminho = tmp_path / nome
    wb.save(caminho)
    return caminho


def test_le_o_modelo_de_exemplo(mapeamento, planilha_modelo):
    produtos, erros = LeitorPlanilha(mapeamento).ler(planilha_modelo)
    assert erros == []
    assert [p.sku for p in produtos] == ["CAM-001-P-AZ", "CAM-001-M-AZ", "TEN-455-42"]
    assert produtos[0].preco == Decimal("89.90")
    assert produtos[0].estoque == 25
    assert produtos[2].imagens[0].principal is True
    assert produtos[2].imagens[2].ordem == 2


def test_cabecalhos_com_acento_caixa_e_pontuacao(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["CÓDIGO", "PRODUTO", "Preço de Venda (R$)", "QTDE"],
        ["X1", "Boné", "R$ 1.234,56", "7"],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert erros == []
    assert produtos[0].sku == "X1"
    assert produtos[0].preco == Decimal("1234.56")
    assert produtos[0].estoque == 7


def test_cabecalho_fora_da_primeira_linha(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["Relatório de produtos - setembro"],
        [],
        ["SKU", "Nome", "Preço"],
        ["A1", "Camisa", "10,00"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    assert [p.sku for p in produtos] == ["A1"]
    assert produtos[0].linha == 4      # the row number Excel shows


def test_sku_duplicado_vira_erro(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome"], ["A1", "Camisa"], ["A1", "Camisa de novo"],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert len(produtos) == 1
    assert len(erros) == 1
    assert "duplicado" in erros[0].mensagem
    assert erros[0].linha == 3


def test_uma_linha_ruim_nao_derruba_as_boas(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Preço"],
        ["A1", "Boa", "10,00"],
        ["A2", "Ruim", "dez reais"],
        ["A3", "Boa também", "12,00"],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert [p.sku for p in produtos] == ["A1", "A3"]
    assert erros[0].linha == 3 and erros[0].campo == "preco"


def test_ean_invalido_e_recusado(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "EAN"], ["A1", "Camisa", "7891234567890"],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert produtos == []
    assert "EAN" in erros[0].mensagem


def test_ean_numerico_nao_vira_notacao_cientifica(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "EAN"], ["A1", "Camisa", 7891234567895],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert erros == []
    assert produtos[0].ean == "7891234567895"


def test_imagens_numeradas_e_delimitadas_juntas(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Imagem 1", "Imagem 2", "Imagens"],
        ["A1", "Camisa", "https://x/1.jpg", "https://x/2.jpg",
         "https://x/3.jpg; https://x/4.jpg"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    urls = [i.url for i in produtos[0].imagens]
    assert urls == [f"https://x/{n}.jpg" for n in (1, 2, 3, 4)]
    assert produtos[0].imagens[0].principal


def test_imagem_repetida_e_descartada(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Imagem 1", "Imagem 2"],
        ["A1", "Camisa", "https://x/1.jpg", "https://x/1.jpg"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    assert len(produtos[0].imagens) == 1


def test_imagem_10_vem_depois_da_2(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Imagem 2", "Imagem 10"],
        ["A1", "Camisa", "https://x/2.jpg", "https://x/10.jpg"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    assert [i.url for i in produtos[0].imagens] == ["https://x/2.jpg", "https://x/10.jpg"]


def test_colunas_desconhecidas_viram_atributos(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Material", "Origem"],
        ["A1", "Camisa", "Algodão", "Nacional"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    assert produtos[0].atributos == {"Material": "Algodão", "Origem": "Nacional"}


def test_ativo_aceita_sim_e_nao(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome", "Ativo"], ["A1", "X", "Sim"], ["A2", "Y", "Não"],
    ])
    produtos, _ = LeitorPlanilha(mapeamento).ler(caminho)
    assert [p.ativo for p in produtos] == [True, False]


def test_linhas_vazias_sao_ignoradas(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [
        ["SKU", "Nome"], ["A1", "X"], [None, None], ["", ""], ["A2", "Y"],
    ])
    produtos, erros = LeitorPlanilha(mapeamento).ler(caminho)
    assert [p.sku for p in produtos] == ["A1", "A2"]
    assert erros == []


def test_planilha_sem_coluna_de_sku(tmp_path, mapeamento):
    caminho = escrever(tmp_path, [["Nome", "Preço"], ["Camisa", "10,00"]])
    with pytest.raises(PlanilhaInvalida, match="SKU"):
        LeitorPlanilha(mapeamento).ler(caminho)
