"""HTTP client for the Magazord API.

Everything endpoint-specific is read from ``config/magazord.yaml`` rather than
hard-coded, so correcting a path or a payload key after checking the official
spec is a config edit, not a code change.

Confirmed about Magazord: it authenticates with HTTP Basic Auth.
Everything else here follows the config file.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class MagazordError(RuntimeError):
    """A request failed in a way retrying will not fix."""

    def __init__(self, mensagem: str, status: int | None = None, corpo: str = ""):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo


class EndpointNaoConfigurado(MagazordError):
    pass


class _Limitador:
    """Enforces a minimum gap between requests, across threads."""

    def __init__(self, intervalo: float):
        self.intervalo = intervalo
        self._lock = threading.Lock()
        self._ultimo = 0.0

    def aguardar(self) -> None:
        if self.intervalo <= 0:
            return
        with self._lock:
            espera = self.intervalo - (time.monotonic() - self._ultimo)
            if espera > 0:
                time.sleep(espera)
            self._ultimo = time.monotonic()


def _percorrer(dados: Any, caminho: str) -> Any:
    """Read a dotted path out of a nested response body."""
    if not caminho:
        return dados
    atual = dados
    for parte in caminho.split("."):
        if isinstance(atual, dict):
            atual = atual.get(parte)
        else:
            return None
    return atual


class MagazordClient:
    def __init__(
        self,
        config: dict[str, Any],
        usuario: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.cfg = config
        self.endpoints = config.get("endpoints") or {}
        http_cfg = config.get("http") or {}

        self.max_retries = int(http_cfg.get("max_retries", 5))
        self.backoff_base = float(http_cfg.get("backoff_base_seconds", 1.0))
        self.backoff_max = float(http_cfg.get("backoff_max_seconds", 60.0))
        self.status_retry = set(http_cfg.get("retry_on_status", [429, 500, 502, 503, 504]))
        self._limitador = _Limitador(float(http_cfg.get("min_interval_seconds", 0.0)))

        self._http = httpx.Client(
            base_url=str(config["base_url"]).rstrip("/"),
            auth=httpx.BasicAuth(usuario, token),
            timeout=float(http_cfg.get("timeout_seconds", 30)),
            headers={"Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> "MagazordClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._http.close()

    # -- low level ---------------------------------------------------------

    def _endpoint(self, nome: str) -> dict[str, Any]:
        ep = self.endpoints.get(nome)
        if not ep or not ep.get("path"):
            raise EndpointNaoConfigurado(
                f"endpoint '{nome}' não está configurado em config/magazord.yaml"
            )
        return ep

    def requisitar(
        self,
        metodo: str,
        caminho: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        data: Any = None,
    ) -> Any:
        """Send one request, retrying transient failures with backoff.

        Honours ``Retry-After`` when the server sends it; otherwise backs off
        exponentially with jitter so parallel workers don't retry in lockstep.
        """
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.max_retries + 1):
            self._limitador.aguardar()
            try:
                resposta = self._http.request(
                    metodo, caminho, params=params, json=json, files=files, data=data
                )
            except httpx.RequestError as exc:
                ultimo_erro = exc
                if tentativa == self.max_retries:
                    raise MagazordError(f"falha de rede após {tentativa} tentativas: {exc}") from exc
                self._esperar(tentativa, None)
                continue

            if resposta.status_code in self.status_retry and tentativa < self.max_retries:
                log.warning(
                    "%s %s devolveu %s (tentativa %d/%d)",
                    metodo, caminho, resposta.status_code, tentativa, self.max_retries,
                )
                self._esperar(tentativa, resposta.headers.get("Retry-After"))
                continue

            if resposta.status_code >= 400:
                raise MagazordError(
                    f"{metodo} {caminho} falhou com HTTP {resposta.status_code}",
                    status=resposta.status_code,
                    corpo=resposta.text[:500],
                )

            if not resposta.content:
                return None
            try:
                return resposta.json()
            except ValueError:
                return resposta.text

        raise MagazordError(f"esgotadas as tentativas para {metodo} {caminho}: {ultimo_erro}")

    def _esperar(self, tentativa: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), self.backoff_max))
                return
            except ValueError:
                pass
        atraso = min(self.backoff_base * (2 ** (tentativa - 1)), self.backoff_max)
        time.sleep(atraso * (0.5 + random.random() / 2))   # jitter

    # -- product operations ------------------------------------------------

    def buscar_por_sku(self, sku: str) -> str | None:
        """Return the Magazord id of an existing product, or None.

        This is what makes the run idempotent against the platform rather than
        only against local state: a product already in the store is updated,
        never duplicated.
        """
        ep = self._endpoint("buscar_produto")
        param = ep.get("sku_query_param", "codigo")
        corpo = self.requisitar(ep.get("method", "GET"), ep["path"], params={param: sku})

        resultados = _percorrer(corpo, ep.get("results_path", ""))
        if isinstance(resultados, dict):
            resultados = [resultados]
        if not isinstance(resultados, list) or not resultados:
            return None

        campo_id = ep.get("id_field", "id")
        for item in resultados:
            if isinstance(item, dict) and item.get(campo_id) is not None:
                return str(item[campo_id])
        return None

    def criar_produto(self, payload: dict[str, Any]) -> str | None:
        ep = self._endpoint("criar_produto")
        corpo = self.requisitar(ep.get("method", "POST"), ep["path"], json=payload)
        campo_id = ep.get("id_field", "id")
        if isinstance(corpo, dict):
            for local in (corpo, corpo.get("data") or {}):
                if isinstance(local, dict) and local.get(campo_id) is not None:
                    return str(local[campo_id])
        return None

    def atualizar_produto(self, id_produto: str, payload: dict[str, Any]) -> None:
        ep = self._endpoint("atualizar_produto")
        self.requisitar(
            ep.get("method", "PUT"),
            ep["path"].format(id=id_produto),
            json=payload,
        )

    def enviar_imagem_url(self, id_produto: str, payload: dict[str, Any]) -> None:
        ep = self._endpoint("criar_imagem")
        self.requisitar(
            ep.get("method", "POST"),
            ep["path"].format(id=id_produto),
            json=payload,
        )

    def enviar_imagem_arquivo(
        self, id_produto: str, arquivo: Path, campo: str, extras: dict[str, Any]
    ) -> None:
        ep = self._endpoint("criar_imagem")
        with arquivo.open("rb") as fh:
            self.requisitar(
                ep.get("method", "POST"),
                ep["path"].format(id=id_produto),
                files={campo: (arquivo.name, fh)},
                data={k: str(v) for k, v in extras.items()},
            )
