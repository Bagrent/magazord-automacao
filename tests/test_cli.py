"""End-to-end runs through the command line, against a mocked API."""

from __future__ import annotations

import httpx
import respx
from openpyxl import Workbook, load_workbook

from magazord_automacao.cli import main


def planilha(tmp_path):
    wb = Workbook()
    wb.active.append(["SKU", "Nome", "Preço", "Estoque", "Imagem 1"])
    wb.active.append(["A1", "Camiseta", "89,90", 10, "https://cdn/1.jpg"])
    wb.active.append(["A2", "Boné", "49,90", 5, "https://cdn/2.jpg"])
    caminho = tmp_path / "produtos.xlsx"
    wb.save(caminho)
    return caminho


def test_validar_aprova_uma_planilha_boa(tmp_path, capsys):
    assert main(["validar", str(planilha(tmp_path))]) == 0
    assert "2 produto(s) válido(s)" in capsys.readouterr().out


def test_validar_sinaliza_linha_ruim(tmp_path, capsys):
    wb = Workbook()
    wb.active.append(["SKU", "Nome", "Preço"])
    wb.active.append(["A1", "Camiseta", "muito caro"])
    caminho = tmp_path / "ruim.xlsx"
    wb.save(caminho)

    assert main(["validar", str(caminho)]) == 1
    assert "rejeitada" in capsys.readouterr().out


def test_enviar_simula_por_padrao(tmp_path, api_config, base, capsys, monkeypatch):
    monkeypatch.setenv("MAGAZORD_BASE_URL", base)
    relatorio = tmp_path / "rel.xlsx"

    with respx.mock(assert_all_called=False) as mock:
        criar = mock.post(f"{base}/site/produto").mock(return_value=httpx.Response(201))
        codigo = main([
            "enviar", str(planilha(tmp_path)),
            "--sem-imagens",
            "--estado", str(tmp_path / "e.sqlite3"),
            "--relatorio", str(relatorio),
        ])

    assert codigo == 0
    assert not criar.called                      # nothing sent without --confirmar
    assert "SIMULAÇÃO" in capsys.readouterr().out
    assert relatorio.exists()


def test_enviar_com_confirmar_cria_os_produtos(tmp_path, api_config, base, monkeypatch):
    monkeypatch.setenv("MAGAZORD_BASE_URL", base)
    monkeypatch.setenv("MAGAZORD_USER", "u")
    monkeypatch.setenv("MAGAZORD_TOKEN", "t")
    relatorio = tmp_path / "rel.xlsx"

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{base}/site/produto").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        criar = mock.post(f"{base}/site/produto").mock(
            return_value=httpx.Response(201, json={"id": 9})
        )
        codigo = main([
            "enviar", str(planilha(tmp_path)), "--confirmar", "--sem-imagens",
            "--estado", str(tmp_path / "e.sqlite3"), "--relatorio", str(relatorio),
        ])

    assert codigo == 0
    assert criar.call_count == 2

    ws = load_workbook(relatorio)["Envio"]
    linhas = list(ws.iter_rows(min_row=2, values_only=True))
    assert [l[1] for l in linhas] == ["A1", "A2"]
    assert {l[3] for l in linhas} == {"criado"}


def test_enviar_sem_credenciais_para_antes(tmp_path, api_config, base, monkeypatch, capsys):
    monkeypatch.setenv("MAGAZORD_BASE_URL", base)
    monkeypatch.delenv("MAGAZORD_USER", raising=False)
    monkeypatch.delenv("MAGAZORD_TOKEN", raising=False)

    codigo = main(["enviar", str(planilha(tmp_path)), "--confirmar",
                   "--estado", str(tmp_path / "e.sqlite3")])
    assert codigo == 2
    assert "credenciais ausentes" in capsys.readouterr().err
