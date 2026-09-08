"""Checking and fetching the product images referenced by the spreadsheet.

A broken image URL is worth catching before the product is created, not after:
otherwise the store ends up with a live product showing a missing photo.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from .models import Imagem

log = logging.getLogger(__name__)


class ImagemInvalida(RuntimeError):
    pass


class ServicoImagens:
    def __init__(self, config: dict[str, Any], cache: Path | None = None):
        self.cfg = config or {}
        self.modo = self.cfg.get("modo", "url")
        self.extensoes = {e.lower() for e in self.cfg.get("extensoes_aceitas", [])}
        self.tamanho_max = float(self.cfg.get("tamanho_maximo_mb", 10)) * 1024 * 1024
        self.validar = bool(self.cfg.get("validar_antes_de_enviar", True))
        self.cache = cache or Path("cache_imagens")
        self._http = httpx.Client(timeout=30, follow_redirects=True)

    def fechar(self) -> None:
        self._http.close()

    def _extensao(self, url: str) -> str:
        return Path(unquote(urlparse(url).path)).suffix.lower()

    def verificar(self, imagem: Imagem) -> None:
        """HEAD the URL and reject it if it is missing, too big, or not an image."""
        if self.extensoes and self._extensao(imagem.url) not in self.extensoes:
            raise ImagemInvalida(
                f"extensão não aceita em {imagem.url} "
                f"(aceitas: {', '.join(sorted(self.extensoes))})"
            )
        if not self.validar:
            return

        try:
            resposta = self._http.head(imagem.url)
            if resposta.status_code == 405:      # server refuses HEAD; fall back
                resposta = self._http.get(imagem.url, headers={"Range": "bytes=0-0"})
        except httpx.RequestError as exc:
            raise ImagemInvalida(f"não foi possível acessar {imagem.url}: {exc}") from exc

        if resposta.status_code >= 400:
            raise ImagemInvalida(f"HTTP {resposta.status_code} em {imagem.url}")

        tipo = resposta.headers.get("content-type", "")
        if tipo and not tipo.startswith("image/"):
            raise ImagemInvalida(f"{imagem.url} devolveu content-type {tipo!r}, esperado image/*")

        tamanho = resposta.headers.get("content-range", "").rpartition("/")[2] or \
            resposta.headers.get("content-length", "")
        if tamanho.isdigit() and int(tamanho) > self.tamanho_max:
            raise ImagemInvalida(
                f"{imagem.url} tem {int(tamanho) / 1024 / 1024:.1f} MB, "
                f"acima do limite de {self.tamanho_max / 1024 / 1024:.0f} MB"
            )

    def verificar_todas(self, imagens: list[Imagem], paralelismo: int = 8) -> list[str]:
        """Check a batch of URLs concurrently. Returns one message per failure."""
        if not imagens:
            return []
        with ThreadPoolExecutor(max_workers=paralelismo) as pool:
            resultados = list(pool.map(self._verificar_capturando, imagens))
        return [m for m in resultados if m]

    def _verificar_capturando(self, imagem: Imagem) -> str | None:
        try:
            self.verificar(imagem)
            return None
        except ImagemInvalida as exc:
            return str(exc)

    def baixar(self, imagem: Imagem) -> Path:
        """Download to a content-addressed cache, for multipart upload mode.

        The same URL used by several SKUs (a shared size chart, say) is fetched
        once.
        """
        nome = hashlib.sha256(imagem.url.encode()).hexdigest()[:32] + (self._extensao(imagem.url) or ".jpg")
        destino = self.cache / nome
        if destino.exists() and destino.stat().st_size > 0:
            return destino

        self.cache.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_suffix(destino.suffix + ".parcial")
        try:
            with self._http.stream("GET", imagem.url) as resposta:
                if resposta.status_code >= 400:
                    raise ImagemInvalida(f"HTTP {resposta.status_code} ao baixar {imagem.url}")
                total = 0
                with parcial.open("wb") as fh:
                    for bloco in resposta.iter_bytes(64 * 1024):
                        total += len(bloco)
                        if total > self.tamanho_max:
                            raise ImagemInvalida(f"{imagem.url} excede o limite de tamanho")
                        fh.write(bloco)
        except ImagemInvalida:
            parcial.unlink(missing_ok=True)
            raise
        except httpx.RequestError as exc:
            parcial.unlink(missing_ok=True)
            raise ImagemInvalida(f"falha ao baixar {imagem.url}: {exc}") from exc

        parcial.replace(destino)   # atomic: never leave a truncated file in cache
        return destino
