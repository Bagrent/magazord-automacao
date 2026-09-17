"""Drawing the catalogue pages.

Each page is a 1080x1920 story-sized image built from the theme: warm gradient,
a dark dome behind the product, the brand at the top, the headline, the product
cut-out, its name, the old and the new price, and the legal footer. The layout
is expressed as fractions of the page, so changing ``pagina.largura/altura`` in
``config/catalogo.yaml`` rescales everything instead of breaking it.

Ready-made art -- the pages a designer already exported -- skips all of this
and goes through :func:`normalizar` instead, which only forces it to the
catalogue's page size so the flipbook has uniform leaves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .fontes import Fontes
from .oferta import Oferta
from .tema import Tema

log = logging.getLogger(__name__)

# -- geometry, as fractions of the page ------------------------------------
MARCA_Y = 0.072
ARCO_TOPO = 0.133
ARCO_RX = 0.454          # of the width
ARCO_RY = 0.354          # of the height
CHAMADA_Y = 0.278
CHAMADA_LARGURA = 0.90
PRODUTO = (0.20, 0.335, 0.80, 0.680)   # x0, y0, x1, y1
NOME_Y = 0.712
NOME_LARGURA = 0.86
LINHA_Y = 0.768
PRECO_DE_Y = 0.810
PRECO_POR_Y = 0.874
PRECOS_X = 0.132
SELO_CENTRO = (0.815, 0.415)
RODAPE_Y = 0.952


# -- small drawing helpers -------------------------------------------------

def _gradiente(tamanho: tuple[int, int], topo, base) -> Image.Image:
    """Vertical two-stop gradient, drawn one row at a time on a 1px column."""
    largura, altura = tamanho
    coluna = Image.new("RGB", (1, altura))
    pixels = coluna.load()
    for y in range(altura):
        t = y / max(1, altura - 1)
        pixels[0, y] = tuple(int(round(topo[c] + (base[c] - topo[c]) * t)) for c in range(3))
    return coluna.resize((largura, altura), Image.BILINEAR)


def _grao(tamanho: tuple[int, int], intensidade: int = 7) -> Image.Image:
    """Faint film grain, so large flat areas do not band on a phone screen."""
    pequeno = Image.effect_noise((tamanho[0] // 2, tamanho[1] // 2), intensidade)
    return pequeno.resize(tamanho, Image.BILINEAR).convert("L")


def _caixas_decorativas(tamanho: tuple[int, int], cor) -> Image.Image:
    """The soft out-of-focus boxes that bleed in from the four corners."""
    largura, altura = tamanho
    camada = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    lado = int(largura * 0.30)
    cantos = [(-lado // 3, -lado // 3), (largura - lado // 2, -lado // 4),
              (-lado // 2, altura - lado // 2), (largura - lado // 3, altura - lado // 3)]
    for i, (x, y) in enumerate(cantos):
        alfa = 70 if i % 2 == 0 else 48
        caixa = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        ImageDraw.Draw(caixa).rounded_rectangle(
            (0, 0, lado - 1, lado - 1), radius=int(lado * 0.10),
            fill=(*cor, alfa), outline=(255, 255, 255, alfa // 2), width=max(2, lado // 90),
        )
        caixa = caixa.rotate(18 if i % 2 == 0 else -22, resample=Image.BICUBIC, expand=True)
        camada.alpha_composite(caixa, (x, y))
    return camada.filter(ImageFilter.GaussianBlur(radius=max(2, largura // 220)))


def _arco(tamanho: tuple[int, int], cor) -> Image.Image:
    """The dark dome: the bottom half of an ellipse, flat along its top edge."""
    largura, altura = tamanho
    cx, topo = largura / 2, altura * ARCO_TOPO
    rx, ry = largura * ARCO_RX, altura * ARCO_RY

    mascara = Image.new("L", tamanho, 0)
    md = ImageDraw.Draw(mascara)
    md.ellipse((cx - rx, topo - ry, cx + rx, topo + ry), fill=255)
    md.rectangle((0, 0, largura, int(topo)), fill=0)
    mascara = mascara.filter(ImageFilter.GaussianBlur(radius=2))

    # Shading across the dome's own span -- darkest at the top, warming up
    # towards the curve -- so it reads as a lit surface, not a flat sticker.
    corpo = _gradiente(
        (largura, altura),
        cor,
        tuple(min(255, int(c * 1.34)) for c in cor),
    )
    camada = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    # Slightly translucent: the dome picks up the warmth of the gradient under it.
    camada.paste(corpo, (0, 0), mascara.point(lambda v: int(v * 0.93)))
    return camada


def _largura(texto: str, fonte: ImageFont.FreeTypeFont) -> int:
    caixa = fonte.getbbox(texto)
    return caixa[2] - caixa[0]


def _ajustar(texto: str, largura_max: float, tamanho_max: int,
             carregar, tamanho_min: int = 8) -> ImageFont.FreeTypeFont:
    """Largest font size at which ``texto`` still fits ``largura_max``."""
    baixo, alto, melhor = tamanho_min, max(tamanho_min, tamanho_max), tamanho_min
    while baixo <= alto:
        meio = (baixo + alto) // 2
        if _largura(texto, carregar(meio)) <= largura_max:
            melhor, baixo = meio, meio + 1
        else:
            alto = meio - 1
    return carregar(melhor)


def _quebrar(texto: str, fonte: ImageFont.FreeTypeFont, largura_max: float) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        tentativa = f"{atual} {palavra}".strip()
        if atual and _largura(tentativa, fonte) > largura_max:
            linhas.append(atual)
            atual = palavra
        else:
            atual = tentativa
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _nome_em_linhas(texto: str, largura_max: float, tamanho_max: int,
                    carregar, max_linhas: int = 3) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Pick the biggest size whose word wrap stays within ``max_linhas``.

    Line count alone is not enough: a single word longer than the page --
    "PROMOÇÕES" on the cover -- wraps to one line and would still bleed off
    both edges, so every line has to measure within the limit as well.
    """
    tamanho = max(13, tamanho_max)
    while tamanho > 12:
        fonte = carregar(tamanho)
        linhas = _quebrar(texto, fonte, largura_max)
        if len(linhas) <= max_linhas and all(_largura(l, fonte) <= largura_max for l in linhas):
            return fonte, linhas
        tamanho = int(tamanho * 0.92) if tamanho > 14 else tamanho - 1
    fonte = carregar(12)
    return fonte, _quebrar(texto, fonte, largura_max)[:max_linhas]


def _encaixar(imagem: Image.Image, caixa: tuple[int, int, int, int]) -> Image.Image:
    """Scale to fit inside the box without cropping or distorting."""
    x0, y0, x1, y1 = caixa
    lim_l, lim_a = max(1, x1 - x0), max(1, y1 - y0)
    escala = min(lim_l / imagem.width, lim_a / imagem.height)
    novo = (max(1, int(imagem.width * escala)), max(1, int(imagem.height * escala)))
    return imagem.resize(novo, Image.LANCZOS)


def _com_sombra(fundo: Image.Image, produto: Image.Image, posicao: tuple[int, int]) -> None:
    """Composite the product onto the page, dropping a soft shadow first."""
    alfa = produto.getchannel("A") if produto.mode == "RGBA" else None
    if alfa is not None:
        desfoque = max(6, produto.width // 22)
        sombra = Image.new("RGBA", fundo.size, (0, 0, 0, 0))
        tinta = Image.new("RGBA", produto.size, (60, 20, 0, 150))
        sombra.paste(tinta, (posicao[0] + desfoque // 2, posicao[1] + desfoque), alfa)
        fundo.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(desfoque)))
    fundo.alpha_composite(produto if produto.mode == "RGBA" else produto.convert("RGBA"), posicao)


def _abrir(caminho: Path) -> Image.Image:
    imagem = Image.open(caminho)
    imagem.load()
    if imagem.mode not in {"RGBA", "LA"}:
        imagem = imagem.convert("RGBA")
    return imagem.convert("RGBA")


# -- page construction -----------------------------------------------------

class Artista:
    """Draws catalogue pages for one theme. Reusable across pages."""

    def __init__(self, tema: Tema, fontes: Fontes | None = None):
        self.tema = tema
        self.fontes = fontes or Fontes(tema.fonte_titulo or None, tema.fonte_texto or None)
        self._fundo: Image.Image | None = None

    # -- layers ------------------------------------------------------------

    def _base(self) -> Image.Image:
        """The background, built once and reused by every page."""
        if self._fundo is None:
            tamanho = self.tema.tamanho
            fundo = _gradiente(tamanho, self.tema.cor("fundo_inicio"),
                               self.tema.cor("fundo_fim")).convert("RGBA")
            fundo.alpha_composite(_caixas_decorativas(tamanho, self.tema.cor("fundo_fim")))
            fundo.alpha_composite(_arco(tamanho, self.tema.cor("arco")))
            grao = _grao(tamanho)
            fundo.paste(Image.new("RGB", tamanho, (255, 255, 255)), (0, 0),
                        grao.point(lambda v: int(v * 0.07)))
            self._fundo = fundo
        return self._fundo.copy()

    def _marca(self, pagina: Image.Image) -> None:
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        if tema.logo and Path(tema.logo).exists():
            logo = _abrir(Path(tema.logo))
            logo = _encaixar(logo, (0, 0, int(largura * 0.46), int(altura * 0.085)))
            pagina.alpha_composite(logo, ((largura - logo.width) // 2,
                                          int(altura * MARCA_Y - logo.height / 2)))
            return
        if not tema.marca_nome and not tema.marca_complemento:
            return

        desenho = ImageDraw.Draw(pagina)
        y = altura * MARCA_Y
        if tema.marca_nome:
            fonte = _ajustar(tema.marca_nome, largura * 0.40, int(altura * 0.062),
                             self.fontes.display)
            desenho.text((largura / 2, y), tema.marca_nome, font=fonte,
                         fill=tema.cor("texto"), anchor="mm")
        if tema.marca_complemento:
            self._espacado(desenho, tema.marca_complemento,
                           self.fontes.corpo(int(altura * 0.021)),
                           y + altura * 0.040, tema.cor("texto"), espaco=int(largura * 0.007))

    def _espacado(self, desenho: ImageDraw.ImageDraw, texto: str,
                  fonte: ImageFont.FreeTypeFont, y: float, cor, espaco: int) -> None:
        """Letter-spaced centred text -- Pillow has no tracking of its own."""
        larguras = [_largura(c, fonte) for c in texto]
        total = sum(larguras) + espaco * (len(texto) - 1)
        x = self.tema.largura / 2 - total / 2
        for caractere, larg in zip(texto, larguras):
            desenho.text((x, y), caractere, font=fonte, fill=cor, anchor="lm")
            x += larg + espaco

    def _chamada(self, pagina: Image.Image, texto: str) -> None:
        if not texto:
            return
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        contorno = max(6, int(altura * 0.010))
        fonte = _ajustar(texto, largura * CHAMADA_LARGURA - contorno * 2,
                         int(altura * 0.085), self.fontes.display)
        ImageDraw.Draw(pagina).text(
            (largura / 2, altura * CHAMADA_Y), texto, font=fonte, anchor="mm",
            fill=tema.cor("destaque"), stroke_width=contorno, stroke_fill=tema.cor("contorno"),
        )

    def _produto(self, pagina: Image.Image, caminho: Path | None,
                 caixa: tuple[float, float, float, float] = PRODUTO) -> None:
        """Place the product photo inside ``caixa``, given as page fractions."""
        largura, altura = self.tema.largura, self.tema.altura
        x0, y0, x1, y1 = (int(largura * caixa[0]), int(altura * caixa[1]),
                          int(largura * caixa[2]), int(altura * caixa[3]))
        if caminho is None or not Path(caminho).exists():
            if caminho is not None:
                log.warning("imagem do produto não encontrada: %s", caminho)
            return
        try:
            imagem = _encaixar(_abrir(Path(caminho)), (x0, y0, x1, y1))
        except OSError as exc:
            log.warning("não foi possível ler %s: %s", caminho, exc)
            return
        posicao = (x0 + (x1 - x0 - imagem.width) // 2, y0 + (y1 - y0 - imagem.height) // 2)
        _com_sombra(pagina, imagem, posicao)

    def _selo(self, pagina: Image.Image, texto: str) -> None:
        """The round badge in the top-right corner (a discount or "NOVO")."""
        if not texto:
            return
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        raio = int(largura * 0.098)
        centro = (int(largura * SELO_CENTRO[0]), int(altura * SELO_CENTRO[1]))
        desenho = ImageDraw.Draw(pagina)
        desenho.ellipse((centro[0] - raio, centro[1] - raio, centro[0] + raio, centro[1] + raio),
                        fill=tema.cor("contorno"))
        linhas = texto.split()
        fonte = _ajustar(max(linhas, key=len), raio * 1.5,
                         int(raio * (0.62 if len(linhas) > 1 else 0.85)), self.fontes.display)
        alt_linha = fonte.size * 1.02
        y = centro[1] - alt_linha * (len(linhas) - 1) / 2
        for linha in linhas:
            desenho.text((centro[0], y), linha, font=fonte, anchor="mm", fill=tema.cor("destaque"))
            y += alt_linha

    def _nome(self, pagina: Image.Image, texto: str) -> None:
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        fonte, linhas = _nome_em_linhas(texto.upper(), largura * NOME_LARGURA,
                                        int(altura * 0.030), self.fontes.corpo)
        desenho = ImageDraw.Draw(pagina)
        alt_linha = fonte.size * 1.35
        y = altura * NOME_Y
        for linha in linhas:
            desenho.text((largura / 2, y), linha, font=fonte, anchor="mm", fill=tema.cor("texto"))
            y += alt_linha

    def _divisor(self, pagina: Image.Image) -> None:
        largura, altura = self.tema.largura, self.tema.altura
        y = int(altura * LINHA_Y)
        camada = Image.new("RGBA", pagina.size, (0, 0, 0, 0))
        ImageDraw.Draw(camada).line((largura * 0.14, y, largura * 0.86, y),
                                    fill=(*self.tema.cor("contorno"), 90),
                                    width=max(2, int(altura * 0.0016)))
        pagina.alpha_composite(camada)

    def _precos(self, pagina: Image.Image, oferta: Oferta) -> None:
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        desenho = ImageDraw.Draw(pagina)
        x = largura * PRECOS_X

        if oferta.preco_de:
            texto = f"{tema.rotulo_de} {oferta.preco_de}".strip()
            fonte = _ajustar(texto, largura * 0.52, int(altura * 0.042), self.fontes.display)
            desenho.text((x, altura * PRECO_DE_Y), texto, font=fonte, anchor="lm",
                         fill=tema.cor("preco_antigo"),
                         stroke_width=max(4, int(altura * 0.0055)),
                         stroke_fill=tema.cor("contorno"))
        if oferta.preco_por:
            texto = f"{tema.rotulo_por}{oferta.preco_por}".strip()
            contorno = max(6, int(altura * 0.009))
            fonte = _ajustar(texto, largura * 0.78 - contorno * 2, int(altura * 0.080),
                             self.fontes.display)
            desenho.text((x, altura * PRECO_POR_Y), texto, font=fonte, anchor="lm",
                         fill=tema.cor("destaque"), stroke_width=contorno,
                         stroke_fill=tema.cor("contorno"))

    def _rodape(self, pagina: Image.Image, linhas: list[str]) -> None:
        if not linhas:
            return
        largura, altura = self.tema.largura, self.tema.altura
        fonte = self.fontes.corpo(int(altura * 0.0165))
        desenho = ImageDraw.Draw(pagina)
        y = altura * RODAPE_Y
        for linha in linhas:
            desenho.text((largura / 2, y), linha, font=fonte, anchor="mm",
                         fill=self.tema.cor("texto"))
            y += fonte.size * 1.45

    # -- public API --------------------------------------------------------

    def oferta(self, oferta: Oferta, selo_desconto: bool = False) -> Image.Image:
        """Render one offer page.

        ``selo_desconto`` adds a "-33%" badge computed from the two prices for
        offers that carry no badge of their own. It is off by default: the badge
        is extra furniture on art that was designed without one.
        """
        pagina = self._base()
        self._marca(pagina)
        self._chamada(pagina, self.tema.chamada)
        self._produto(pagina, oferta.imagem)
        selo = oferta.selo or (f"-{oferta.desconto}%" if (selo_desconto and oferta.desconto) else "")
        self._selo(pagina, selo)
        self._nome(pagina, oferta.nome)
        self._divisor(pagina)
        self._precos(pagina, oferta)
        rodape = list(self.tema.rodape)
        if oferta.observacao:
            rodape = [oferta.observacao, *rodape]
        self._rodape(pagina, rodape)
        return pagina.convert("RGB")

    def capa(self, titulo: str | None = None, subtitulo: str | None = None,
             imagem: Path | None = None) -> Image.Image:
        """Render the cover: brand, catalogue title, subtitle, optional photo.

        Laid out top-down rather than at fixed heights, because the title is
        whatever the shop called the catalogue -- one line or three -- and the
        photo has to start below wherever it ended.
        """
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        pagina = self._base()
        self._marca(pagina)
        desenho = ImageDraw.Draw(pagina)

        contorno = max(5, int(altura * 0.008))
        texto_titulo = (titulo or tema.titulo).upper()
        fonte, linhas = _nome_em_linhas(texto_titulo, largura * 0.84 - contorno * 2,
                                        int(altura * 0.078), self.fontes.display, max_linhas=3)
        alt_linha = fonte.size * 1.08
        y = altura * 0.185 + alt_linha / 2
        for linha in linhas:
            desenho.text((largura / 2, y), linha, font=fonte, anchor="mm",
                         fill=tema.cor("destaque"), stroke_width=contorno,
                         stroke_fill=tema.cor("contorno"))
            y += alt_linha
        fim_titulo = (y - alt_linha / 2 + contorno) / altura

        texto_sub = subtitulo if subtitulo is not None else tema.subtitulo
        topo_legenda = 0.82 if texto_sub else 0.90

        if imagem and Path(imagem).exists():
            self._produto(pagina, Path(imagem),
                          (0.24, min(fim_titulo + 0.03, 0.55), 0.76, topo_legenda - 0.04))

        if texto_sub:
            fonte_sub, linhas_sub = _nome_em_linhas(texto_sub, largura * 0.80,
                                                    int(altura * 0.030), self.fontes.corpo,
                                                    max_linhas=2)
            y = altura * topo_legenda
            for linha in linhas_sub:
                desenho.text((largura / 2, y), linha, font=fonte_sub, anchor="mm",
                             fill=tema.cor("texto"))
                y += fonte_sub.size * 1.4

        self._rodape(pagina, list(tema.rodape))
        return pagina.convert("RGB")

    def aviso(self, texto: str) -> Image.Image:
        """A plain page carrying one message -- used for the closing page."""
        tema, largura, altura = self.tema, self.tema.largura, self.tema.altura
        pagina = self._base()
        self._marca(pagina)
        fonte, linhas = _nome_em_linhas(texto, largura * 0.80, int(altura * 0.048),
                                        self.fontes.display, max_linhas=4)
        desenho = ImageDraw.Draw(pagina)
        alt_linha = fonte.size * 1.25
        y = altura * 0.45 - alt_linha * (len(linhas) - 1) / 2
        for linha in linhas:
            desenho.text((largura / 2, y), linha, font=fonte, anchor="mm",
                         fill=tema.cor("texto"))
            y += alt_linha
        self._rodape(pagina, list(tema.rodape))
        return pagina.convert("RGB")


def normalizar(caminho: Path, tamanho: tuple[int, int],
               fundo: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """Force ready-made art to the catalogue page size.

    Art exported at the right aspect ratio is simply resampled. Anything else
    is centred on a background sampled from its own corner, so a page that is
    slightly the wrong shape gets a matching border instead of white bars.
    """
    imagem = Image.open(caminho)
    imagem.load()
    if imagem.mode in {"RGBA", "LA", "P"}:
        chapa = Image.new("RGB", imagem.size, fundo)
        convertida = imagem.convert("RGBA")
        chapa.paste(convertida, (0, 0), convertida.getchannel("A"))
        imagem = chapa
    else:
        imagem = imagem.convert("RGB")

    alvo_l, alvo_a = tamanho
    proporcao = imagem.width / imagem.height
    if abs(proporcao - alvo_l / alvo_a) < 0.01:
        return imagem.resize(tamanho, Image.LANCZOS)

    encaixada = _encaixar(imagem, (0, 0, alvo_l, alvo_a))
    borda = imagem.resize((1, 1), Image.BILINEAR).getpixel((0, 0))
    chapa = Image.new("RGB", tamanho, borda if isinstance(borda, tuple) else fundo)
    chapa.paste(encaixada, ((alvo_l - encaixada.width) // 2, (alvo_a - encaixada.height) // 2))
    return chapa
