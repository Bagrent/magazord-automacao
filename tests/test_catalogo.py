"""The catalogue generator: sources, page art, PDF, and the flipbook viewer.

No network and no fixtures on disk -- the product photos these tests draw with
are generated in the test itself, so the suite runs anywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="o catálogo depende do Pillow (extra 'catalogo')")

from PIL import Image  # noqa: E402

from magazord_automacao.catalogo import (  # noqa: E402
    Opcoes, Oferta, Tema, construir, de_pasta, de_planilha,
)
from magazord_automacao.catalogo.arte import (  # noqa: E402
    Artista, _nome_em_linhas, _largura, normalizar,
)
from magazord_automacao.catalogo.fontes import Fontes  # noqa: E402
from magazord_automacao.catalogo.fontes_dados import ordem_natural  # noqa: E402
from magazord_automacao.catalogo.modelo_ofertas import gerar as gerar_modelo  # noqa: E402
from magazord_automacao.catalogo import pdf as pdf_mod  # noqa: E402


@pytest.fixture
def tema():
    # Quarter size: same layout, a quarter of the drawing time.
    return Tema.de_dict({"titulo": "Catálogo de Teste",
                         "marca": {"nome": "LOJA", "complemento": "TESTE"},
                         "pagina": {"largura": 270, "altura": 480},
                         "rodape": ["Imagens meramente ilustrativas."]})


@pytest.fixture
def foto(tmp_path) -> Path:
    caminho = tmp_path / "foto.png"
    Image.new("RGBA", (200, 500), (30, 30, 34, 255)).save(caminho)
    return caminho


# -- sources ---------------------------------------------------------------

def test_ordem_natural_conta_como_gente(tmp_path):
    for nome in ("pagina10.jpg", "pagina2.jpg", "pagina1.jpg"):
        Image.new("RGB", (10, 18), "white").save(tmp_path / nome)
    assert [c.name for c in de_pasta(tmp_path)] == ["pagina1.jpg", "pagina2.jpg", "pagina10.jpg"]


def test_ordem_natural_e_estavel_sem_numeros():
    assert ordem_natural(Path("b.jpg")) > ordem_natural(Path("a.jpg"))


def test_pasta_sem_imagens_reclama(tmp_path):
    (tmp_path / "leiame.txt").write_text("nada aqui")
    with pytest.raises(FileNotFoundError):
        de_pasta(tmp_path)


def test_planilha_csv_le_apelidos_de_coluna(tmp_path):
    csv = tmp_path / "ofertas.csv"
    csv.write_text(
        "Nome do Produto;DE;POR;Foto;URL\n"
        "Base Real Filter;59,99;39,99;foto.png;https://loja.exemplo/base\n"
        "Gloss Chocochilli;;29,99;;\n",
        encoding="utf-8",
    )
    Image.new("RGB", (10, 10), "white").save(tmp_path / "foto.png")

    ofertas = de_planilha(csv)
    assert [o.nome for o in ofertas] == ["Base Real Filter", "Gloss Chocochilli"]
    assert ofertas[0].preco_de == "59,99" and ofertas[0].preco_por == "39,99"
    assert ofertas[0].imagem == tmp_path / "foto.png"
    assert ofertas[0].link == "https://loja.exemplo/base"
    # A missing photo is a warning, never a crash: the page still gets made.
    assert ofertas[1].preco_de is None and ofertas[1].imagem is None


def test_planilha_sem_coluna_de_nome_e_recusada(tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("Coluna A;Coluna B\n1;2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nome do produto"):
        de_planilha(csv)


def test_modelo_de_ofertas_le_de_volta(tmp_path):
    caminho = gerar_modelo(tmp_path / "modelo.xlsx")
    ofertas = de_planilha(caminho)
    assert len(ofertas) == 5
    assert all(o.nome and o.preco_por for o in ofertas)
    # The column help lives in cell comments, so it must not read back as a page.
    assert not any("coluna" in o.nome.lower() for o in ofertas)


# -- page art --------------------------------------------------------------

def test_pagina_sai_no_tamanho_do_tema(tema, foto):
    pagina = Artista(tema).oferta(Oferta("Base Real Filter", "59,99", "39,99", foto))
    assert pagina.size == tema.tamanho
    assert pagina.mode == "RGB"
    assert len(pagina.getcolors(maxcolors=100000) or []) > 40   # it is not a flat rectangle


def test_desconto_so_conta_quando_ha_queda_real():
    assert Oferta("x", "59,99", "39,99").desconto == 33
    assert Oferta("x", None, "39,99").desconto is None
    assert Oferta("x", "19,99", "39,99").desconto is None
    assert Oferta("x", "a partir de", "39,99").desconto is None


def test_nome_comprido_nunca_estoura_a_largura():
    fontes = Fontes()
    limite = 400
    for texto in ("PROMOÇÕES",
                  "BASE LIQUIDA FRANCINY EHLKE REAL FILTER",
                  "ANTIDESTEMIDAMENTECONSTITUCIONALISSIMAMENTE"):
        fonte, linhas = _nome_em_linhas(texto, limite, 120, fontes.display, max_linhas=3)
        assert linhas
        assert all(_largura(linha, fonte) <= limite for linha in linhas), texto


def test_arte_pronta_e_ajustada_ao_tamanho_da_pagina(tmp_path, tema):
    quadrada = tmp_path / "quadrada.png"
    Image.new("RGB", (600, 600), (10, 120, 200)).save(quadrada)
    ajustada = normalizar(quadrada, tema.tamanho)
    assert ajustada.size == tema.tamanho


# -- assembly --------------------------------------------------------------

def test_construir_entrega_paginas_pdf_e_visualizador(tmp_path, tema, foto):
    ofertas = [Oferta("Base Real Filter", "59,99", "39,99", foto, link="https://loja/1"),
               Oferta("Gloss Chocochilli", "59,99", "29,99", foto)]
    saida = tmp_path / "catalogo"

    resultado = construir(ofertas, tema, Opcoes(saida=saida, contracapa="Peça pelo WhatsApp"))

    assert resultado.total == 4                      # capa + 2 ofertas + contracapa
    assert resultado.html == saida / "index.html"
    assert resultado.pdf and resultado.pdf.exists()
    assert [c.name for c in resultado.paginas] == [f"pagina-0{i}.jpg" for i in range(1, 5)]
    assert all((saida / "miniaturas" / c.name).exists() for c in resultado.paginas)
    assert (saida / "flipbook.js").exists() and (saida / "flipbook.css").exists()


def test_pdf_tem_uma_pagina_por_arte(tmp_path, tema, foto):
    resultado = construir([Oferta("A", imagem=foto), Oferta("B"), Oferta("C")],
                          tema, Opcoes(saida=tmp_path / "c", capa=False))
    bruto = resultado.pdf.read_bytes()
    assert bruto.startswith(b"%PDF")
    assert b"/Count 3" in bruto


def test_visualizador_conhece_todas_as_paginas(tmp_path, tema, foto):
    resultado = construir([Oferta("Base", "59,99", "39,99", foto, link="https://loja/1")],
                          tema, Opcoes(saida=tmp_path / "c"))
    html = resultado.html.read_text(encoding="utf-8")

    dados = json.loads(re.search(r"window\.CATALOGO = (\{.*?\});", html, re.S).group(1))
    assert len(dados["paginas"]) == resultado.total
    assert dados["paginas"][0]["src"] == "paginas/pagina-01.jpg"
    assert dados["paginas"][1]["link"] == "https://loja/1"
    assert dados["proporcao"] == pytest.approx(tema.largura / tema.altura)
    assert 'href="catalogo.pdf"' in html


def test_um_arquivo_nao_depende_de_nada_externo(tmp_path, tema, foto):
    resultado = construir([Oferta("Base", "59,99", "39,99", foto)],
                          tema, Opcoes(saida=tmp_path / "c", um_arquivo=True, gerar_pdf=False))
    html = resultado.html.read_text(encoding="utf-8")

    assert resultado.html.suffix == ".html"
    assert "paginas/pagina-01.jpg" not in html
    assert 'src="flipbook.js"' not in html and 'href="flipbook.css"' not in html
    assert html.count("data:image/jpeg;base64,") >= 2      # page + thumbnail
    assert "<script>" in html and "<style>" in html


def test_rodar_de_novo_nao_deixa_paginas_velhas(tmp_path, tema, foto):
    saida = tmp_path / "c"
    construir([Oferta(f"Item {i}", imagem=foto) for i in range(4)], tema,
              Opcoes(saida=saida, capa=False))
    resultado = construir([Oferta("Só um", imagem=foto)], tema, Opcoes(saida=saida, capa=False))

    assert resultado.total == 1
    assert sorted(p.name for p in (saida / "paginas").glob("*.jpg")) == ["pagina-01.jpg"]


def test_catalogo_de_artes_prontas_nao_redesenha(tmp_path, tema):
    artes = tmp_path / "artes"
    artes.mkdir()
    for i in (1, 2):
        Image.new("RGB", tema.tamanho, (i * 60, 40, 20)).save(artes / f"{i:02d}-arte.png")

    resultado = construir(de_pasta(artes), tema, Opcoes(saida=tmp_path / "c"))

    assert resultado.total == 2                       # nenhuma capa foi inventada
    assert Image.open(resultado.paginas[0]).size == tema.tamanho


def test_fonte_vazia_e_recusada_com_clareza(tema):
    with pytest.raises(ValueError, match="nenhuma oferta"):
        construir([], tema, Opcoes())


def test_pdf_sem_paginas_e_recusado(tmp_path):
    with pytest.raises(ValueError):
        pdf_mod.gerar([], tmp_path / "x.pdf")
