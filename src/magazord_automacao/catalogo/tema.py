"""The catalogue's visual identity, read from ``config/catalogo.yaml``.

Kept as data rather than constants in the drawing code so the shop can restyle
the catalogue -- colours, brand, footer, headline -- without touching Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ConfigInvalida, carregar_yaml

Cor = tuple[int, int, int]

PADRAO: dict[str, Any] = {
    "titulo": "Catálogo de Promoções",
    "subtitulo": "",
    "marca": {"nome": "", "complemento": "", "logo": ""},
    "pagina": {"largura": 1080, "altura": 1920, "chamada": "PROMOÇÕES"},
    "cores": {
        "fundo_inicio": "#F6A340",
        "fundo_fim": "#EC7A18",
        "arco": "#A23B10",
        "destaque": "#F07817",
        "texto": "#FFFFFF",
        "contorno": "#FFFFFF",
        "preco_antigo": "#111111",
    },
    "rodape": [],
    "rotulos": {"preco_de": "DE: R$", "preco_por": "POR:"},
    "fontes": {"titulo": "", "texto": ""},
}


def cor(valor: str | tuple[int, int, int]) -> Cor:
    """``"#F07817"`` -> ``(240, 120, 23)``. Accepts #rgb, #rrggbb and tuples."""
    if isinstance(valor, (tuple, list)):
        r, g, b = (int(c) for c in tuple(valor)[:3])
        return r, g, b
    texto = str(valor).strip().lstrip("#")
    if len(texto) == 3:
        texto = "".join(c * 2 for c in texto)
    if len(texto) != 6:
        raise ConfigInvalida(f"cor inválida: {valor!r} (use #rrggbb)")
    try:
        return tuple(int(texto[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ConfigInvalida(f"cor inválida: {valor!r}") from exc


def _fundir(base: dict[str, Any], sobre: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``sobre`` onto ``base``; empty strings do not override."""
    saida = dict(base)
    for chave, valor in (sobre or {}).items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = _fundir(saida[chave], valor)
        elif valor not in (None, ""):
            saida[chave] = valor
    return saida


@dataclass
class Tema:
    titulo: str = "Catálogo de Promoções"
    subtitulo: str = ""
    largura: int = 1080
    altura: int = 1920
    chamada: str = "PROMOÇÕES"
    marca_nome: str = ""
    marca_complemento: str = ""
    logo: Path | None = None
    fonte_titulo: str = ""
    fonte_texto: str = ""
    rodape: list[str] = field(default_factory=list)
    rotulo_de: str = "DE: R$"
    rotulo_por: str = "POR:"
    cores: dict[str, Cor] = field(default_factory=dict)

    @classmethod
    def carregar(cls, caminho: Path | None = None, **sobrepor: Any) -> "Tema":
        dados = dict(PADRAO)
        if caminho is not None:
            dados = _fundir(dados, carregar_yaml(caminho, expandir_env=False))
        else:
            padrao = Path("config/catalogo.yaml")
            embutido = Path(__file__).resolve().parents[3] / "config" / "catalogo.yaml"
            for candidato in (embutido, padrao):
                if candidato.exists():
                    dados = _fundir(dados, carregar_yaml(candidato, expandir_env=False))
                    break
        dados = _fundir(dados, {k: v for k, v in sobrepor.items() if v not in (None, "")})
        return cls.de_dict(dados)

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> "Tema":
        dados = _fundir(PADRAO, dados)
        pagina = dados["pagina"]
        marca = dados["marca"]
        logo = marca.get("logo") or ""
        return cls(
            titulo=str(dados.get("titulo") or PADRAO["titulo"]),
            subtitulo=str(dados.get("subtitulo") or ""),
            largura=int(pagina.get("largura") or 1080),
            altura=int(pagina.get("altura") or 1920),
            chamada=str(pagina.get("chamada") or ""),
            marca_nome=str(marca.get("nome") or ""),
            marca_complemento=str(marca.get("complemento") or ""),
            logo=Path(logo) if logo else None,
            fonte_titulo=str((dados.get("fontes") or {}).get("titulo") or ""),
            fonte_texto=str((dados.get("fontes") or {}).get("texto") or ""),
            rodape=[str(l) for l in (dados.get("rodape") or [])],
            rotulo_de=str((dados.get("rotulos") or {}).get("preco_de") or ""),
            rotulo_por=str((dados.get("rotulos") or {}).get("preco_por") or ""),
            cores={nome: cor(valor) for nome, valor in (dados.get("cores") or {}).items()},
        )

    def cor(self, nome: str) -> Cor:
        if nome in self.cores:
            return self.cores[nome]
        return cor(PADRAO["cores"][nome])

    @property
    def tamanho(self) -> tuple[int, int]:
        return self.largura, self.altura
