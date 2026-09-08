"""Loading of the two YAML config files, with ${ENV_VAR} expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]


def _pasta_config() -> Path:
    """Where the YAML files live.

    Normally the ``config/`` directory of the checkout. When the package is
    installed elsewhere (a venv, a scheduled job), fall back to ``config/``
    beside the current working directory so the run still finds them.
    """
    do_projeto = RAIZ / "config"
    if do_projeto.is_dir():
        return do_projeto
    return Path.cwd() / "config"


CONFIG_PADRAO = _pasta_config()

_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ConfigInvalida(RuntimeError):
    pass


def _expandir(no: Any) -> Any:
    """Recursively replace ${VAR} in every string with its environment value."""
    if isinstance(no, str):
        def troca(m: re.Match[str]) -> str:
            valor = os.environ.get(m.group(1))
            if valor is None:
                raise ConfigInvalida(
                    f"variável de ambiente {m.group(1)} não está definida "
                    f"(copie .env.example para .env e preencha)"
                )
            return valor
        return _ENV.sub(troca, no)
    if isinstance(no, dict):
        return {k: _expandir(v) for k, v in no.items()}
    if isinstance(no, list):
        return [_expandir(v) for v in no]
    return no


def carregar_yaml(caminho: Path, expandir_env: bool = True) -> dict[str, Any]:
    if not caminho.exists():
        raise ConfigInvalida(f"arquivo de configuração não encontrado: {caminho}")
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    if not isinstance(dados, dict):
        raise ConfigInvalida(f"{caminho} não contém um mapeamento YAML")
    return _expandir(dados) if expandir_env else dados


def carregar_mapeamento(caminho: Path | None = None) -> dict[str, Any]:
    """Column mapping. No env expansion -- it holds no secrets."""
    return carregar_yaml(caminho or _pasta_config() / "mapeamento.yaml", expandir_env=False)


def carregar_api(caminho: Path | None = None) -> dict[str, Any]:
    """API binding. Expands ${MAGAZORD_BASE_URL} and friends."""
    cfg = carregar_yaml(caminho or _pasta_config() / "magazord.yaml")
    for obrigatorio in ("base_url", "endpoints", "campos"):
        if obrigatorio not in cfg:
            raise ConfigInvalida(f"seção '{obrigatorio}' ausente em {caminho}")
    return cfg


def carregar_dotenv(caminho: Path | None = None) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    arquivo = caminho or RAIZ / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))
