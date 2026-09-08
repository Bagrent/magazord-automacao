from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from magazord_automacao.config import carregar_mapeamento, carregar_yaml  # noqa: E402

BASE = "https://loja.teste.local/api/v2"


@pytest.fixture
def mapeamento():
    return carregar_mapeamento(RAIZ / "config" / "mapeamento.yaml")


@pytest.fixture
def api_config(monkeypatch):
    monkeypatch.setenv("MAGAZORD_BASE_URL", BASE)
    return carregar_yaml(RAIZ / "config" / "magazord.yaml")


@pytest.fixture
def planilha_modelo():
    caminho = RAIZ / "exemplos" / "modelo_produtos.xlsx"
    if not caminho.exists():
        sys.path.insert(0, str(RAIZ / "exemplos"))
        from gerar_modelo import gerar
        gerar(caminho)
    return caminho


@pytest.fixture
def base():
    """Base URL the mocked Magazord API is served from."""
    return BASE
