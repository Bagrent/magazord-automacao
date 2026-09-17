"""Command line entry point.

    magazord modelo                      gera uma planilha de exemplo
    magazord validar  produtos.xlsx      lê e critica a planilha, sem rede
    magazord enviar   produtos.xlsx      simula o envio (padrão seguro)
    magazord enviar   produtos.xlsx --confirmar    envia de verdade
    magazord estado                      mostra o que já foi enviado
    magazord catalogo ofertas.xlsx       monta o catálogo folheável + PDF
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .client import MagazordClient, MagazordError
from .config import ConfigInvalida, carregar_api, carregar_dotenv, carregar_mapeamento
from .estado import Estado
from .imagens import ServicoImagens
from .mapper import MapeadorPayload
from .pipeline import Opcoes, Pipeline
from .planilha import LeitorPlanilha, PlanilhaInvalida
from . import relatorio

log = logging.getLogger("magazord")


def _configurar_log(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _ler_planilha(caminho: Path, mapeamento_path: Path | None):
    leitor = LeitorPlanilha(carregar_mapeamento(mapeamento_path))
    return leitor.ler(caminho)


def _mostrar_erros(erros) -> None:
    if not erros:
        return
    print(f"\n{len(erros)} linha(s) rejeitada(s) na leitura:")
    for e in erros[:30]:
        coluna = f" [{e.campo}]" if e.campo else ""
        print(f"  linha {e.linha:>4} {e.sku or '-':<20}{coluna} {e.mensagem}")
    if len(erros) > 30:
        print(f"  ... e mais {len(erros) - 30}. A lista completa vai no relatório.")


# -- commands ---------------------------------------------------------------

def cmd_modelo(args: argparse.Namespace) -> int:
    if args.tipo == "ofertas":
        from .catalogo.modelo_ofertas import gerar

        destino = gerar(Path(args.destino or "exemplos/modelo_ofertas.xlsx"))
        print(f"modelo gerado: {destino}")
        print("Uma linha por oferta. Passe o arquivo para 'magazord catalogo'.")
        return 0

    from .modelo import gerar

    destino = gerar(Path(args.destino or "exemplos/modelo_produtos.xlsx"))
    print(f"modelo gerado: {destino}")
    print("Preencha uma linha por SKU. As colunas 'Imagem 1..N' recebem links públicos.")
    return 0


def cmd_catalogo(args: argparse.Namespace) -> int:
    """Build the flipbook catalogue from art, an offers sheet, or the product sheet."""
    try:
        from .catalogo import Opcoes, Tema, construir, de_pasta, de_planilha, de_produtos
    except ImportError as exc:                    # Pillow is an optional extra
        print(f"erro: o catálogo precisa do Pillow ({exc}).\n"
              f'      instale com: pip install -e ".[catalogo]"', file=sys.stderr)
        return 2

    entrada = Path(args.entrada)
    if not entrada.exists():
        print(f"erro: não encontrei {entrada}", file=sys.stderr)
        return 2

    saida = Path(args.saida)
    if entrada.is_dir():
        # Writing the pages inside the art folder would make the next run read
        # its own output back as if it were new art.
        try:
            dentro = saida.resolve().is_relative_to(entrada.resolve())
        except OSError:
            dentro = False
        if dentro:
            print(f"erro: --saida ({saida}) está dentro da pasta das artes ({entrada}).\n"
                  f"      Na próxima execução as páginas geradas entrariam como arte nova.\n"
                  f"      Escolha uma pasta de saída fora, por exemplo: "
                  f'"{entrada.parent / (entrada.name + " - catalogo")}"', file=sys.stderr)
            return 2
        fonte: list = de_pasta(entrada)
        print(f"{len(fonte)} imagem(ns) em {entrada}, na ordem dos nomes dos arquivos.")
    elif args.produtos:
        fonte = de_produtos(entrada, carregar_mapeamento(args.mapeamento),
                            somente_promocao=not args.todos)
        print(f"{len(fonte)} produto(s) da planilha viraram páginas.")
    else:
        fonte = de_planilha(entrada, args.fotos)
        print(f"{len(fonte)} oferta(s) lidas de {entrada}.")

    tema = Tema.carregar(args.tema, titulo=args.titulo, subtitulo=args.subtitulo)
    if args.tamanho:
        try:
            largura, altura = (int(v) for v in args.tamanho.lower().split("x"))
        except ValueError:
            print(f"erro: --tamanho espera algo como 1080x1920, veio {args.tamanho!r}",
                  file=sys.stderr)
            return 2
        tema.largura, tema.altura = largura, altura
    if args.marca:
        tema.marca_nome = args.marca
    if args.logo:
        tema.logo = Path(args.logo)
    if args.chamada is not None:
        tema.chamada = args.chamada

    opcoes = Opcoes(
        saida=saida,
        capa=not args.sem_capa,
        capa_imagem=Path(args.capa) if args.capa else None,
        contracapa=args.contracapa,
        um_arquivo=args.um_arquivo,
        gerar_pdf=not args.sem_pdf,
        selo_desconto=args.selo_desconto,
        qualidade=args.qualidade,
        limite=args.limite,
        rotulo_link=args.rotulo_link,
        preservar_originais=not args.recodificar,
        pdf_largura=args.pdf_largura,
        tamanho_da_arte=not args.tamanho,
    )

    resultado = construir(fonte, tema, opcoes)

    print(f"\n{resultado.total} página(s) em {resultado.segundos:.1f}s")
    print(f"  folhear:  {resultado.html}")
    if resultado.pdf:
        print(f"  PDF:      {resultado.pdf}")
    if not opcoes.um_arquivo:
        print(f"  imagens:  {resultado.pasta / 'paginas'}")
        print("\nPara publicar, suba a pasta inteira em qualquer hospedagem estática "
              "(GitHub Pages, Netlify, o servidor da loja) e compartilhe o link do index.html.")
    else:
        print("\nArquivo único: dá para mandar por e-mail ou WhatsApp e abre offline.")
    return 0


def cmd_validar(args: argparse.Namespace) -> int:
    produtos, erros = _ler_planilha(Path(args.planilha), args.mapeamento)

    print(f"\n{len(produtos)} produto(s) válido(s), {len(erros)} linha(s) rejeitada(s).")
    sem_imagem = [p for p in produtos if not p.imagens]
    sem_preco = [p for p in produtos if p.preco is None]
    if sem_imagem:
        print(f"  atenção: {len(sem_imagem)} produto(s) sem nenhuma imagem "
              f"(ex.: {', '.join(p.sku for p in sem_imagem[:5])})")
    if sem_preco:
        print(f"  atenção: {len(sem_preco)} produto(s) sem preço "
              f"(ex.: {', '.join(p.sku for p in sem_preco[:5])})")
    print(f"  {sum(len(p.imagens) for p in produtos)} imagem(ns) referenciada(s)")

    _mostrar_erros(erros)
    return 1 if erros else 0


def cmd_enviar(args: argparse.Namespace) -> int:
    carregar_dotenv()
    api = carregar_api(args.config)

    produtos, erros_leitura = _ler_planilha(Path(args.planilha), args.mapeamento)
    _mostrar_erros(erros_leitura)
    if not produtos:
        print("nenhum produto válido para enviar.")
        return 1

    simular = not args.confirmar
    if simular:
        print("\n*** SIMULAÇÃO — nada será enviado. Use --confirmar para enviar de verdade. ***")

    usuario = os.environ.get(api["auth"].get("user_env", "MAGAZORD_USER"), "")
    token = os.environ.get(api["auth"].get("token_env", "MAGAZORD_TOKEN"), "")
    if not simular and not (usuario and token):
        print("erro: credenciais ausentes. Preencha MAGAZORD_USER e MAGAZORD_TOKEN no .env",
              file=sys.stderr)
        return 2

    opcoes = Opcoes(
        tamanho_lote=args.lote,
        paralelismo=args.paralelismo,
        simular=simular,
        forcar=args.forcar,
        parar_no_erro=args.parar_no_erro,
        limite=args.limite,
        enviar_imagens=not args.sem_imagens,
    )

    servico_imagens = ServicoImagens(api.get("imagens") or {}, Path(args.cache_imagens))
    try:
        with MagazordClient(api, usuario, token) as client, Estado(Path(args.estado)) as estado:
            pipeline = Pipeline(client, MapeadorPayload(api), servico_imagens, estado, opcoes)
            resumo = pipeline.executar(produtos, erros_leitura)
    finally:
        servico_imagens.fechar()

    destino = Path(args.relatorio) if args.relatorio else relatorio.caminho_padrao(Path("saida"))
    caminho = relatorio.gerar(resumo, destino)

    print(f"\nresumo do envio ({resumo.total_processado} produtos):")
    print(relatorio.resumo_texto(resumo))
    print(f"\nrelatório: {caminho}")

    return 1 if (resumo.erros or erros_leitura) else 0


def cmd_estado(args: argparse.Namespace) -> int:
    caminho = Path(args.estado)
    if not caminho.exists():
        print(f"nenhum estado ainda em {caminho} — nada foi enviado a partir desta máquina.")
        return 0

    with Estado(caminho) as estado:
        if args.esquecer:
            achou = estado.esquecer(args.esquecer)
            print(f"SKU {args.esquecer}: {'removido do estado' if achou else 'não estava no estado'}")
            print("O próximo envio vai tratá-lo como novo." if achou else "")
            return 0

        print(f"{estado.total()} SKU(s) registrados em {caminho}\n")
        print(f"{'SKU':<22}{'ID':<12}{'IMGS':<6}ATUALIZADO EM")
        for reg in estado.listar(args.limite):
            print(f"{reg['sku']:<22}{reg['id_magazord'] or '-':<12}"
                  f"{reg['imagens']:<6}{reg['atualizado_em']}")
    return 0


# -- argument parsing -------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="magazord",
        description="Envio em lote de produtos para a Magazord a partir de uma planilha Excel.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verboso", action="store_true", help="log detalhado")
    sub = p.add_subparsers(dest="comando", required=True)

    m = sub.add_parser("modelo", help="gera uma planilha de exemplo no formato esperado")
    m.add_argument("--tipo", choices=["produtos", "ofertas"], default="produtos",
                   help="produtos: planilha de envio; ofertas: planilha do catálogo")
    m.add_argument("--destino", default=None)
    m.set_defaults(func=cmd_modelo)

    v = sub.add_parser("validar", help="lê e critica a planilha, sem acessar a rede")
    v.add_argument("planilha")
    v.add_argument("--mapeamento", type=Path, default=None)
    v.set_defaults(func=cmd_validar)

    e = sub.add_parser("enviar", help="envia os produtos (simula por padrão)")
    e.add_argument("planilha")
    e.add_argument("--confirmar", action="store_true",
                   help="envia de verdade; sem esta flag apenas simula")
    e.add_argument("--forcar", action="store_true",
                   help="reenvia mesmo os produtos sem alteração desde o último envio")
    e.add_argument("--lote", type=int, default=50, help="produtos por lote (padrão: 50)")
    e.add_argument("--paralelismo", type=int, default=4,
                   help="envios simultâneos dentro do lote (padrão: 4)")
    e.add_argument("--limite", type=int, default=None,
                   help="processa no máximo N produtos, para um teste piloto")
    e.add_argument("--sem-imagens", action="store_true", help="não envia imagens")
    e.add_argument("--parar-no-erro", action="store_true",
                   help="interrompe assim que um lote tiver falhas")
    e.add_argument("--config", type=Path, default=None)
    e.add_argument("--mapeamento", type=Path, default=None)
    e.add_argument("--estado", default="estado.sqlite3")
    e.add_argument("--cache-imagens", default="cache_imagens")
    e.add_argument("--relatorio", default=None)
    e.set_defaults(func=cmd_enviar)

    c = sub.add_parser(
        "catalogo",
        help="monta um catálogo folheável (HTML) e um PDF",
        description="A entrada pode ser uma pasta com as artes prontas, uma planilha "
                    "de ofertas, ou a própria planilha de produtos (com --produtos).",
    )
    c.add_argument("entrada", help="pasta de imagens, planilha de ofertas (.xlsx/.csv)")
    c.add_argument("--saida", default="saida/catalogo", help="pasta de destino")
    c.add_argument("--titulo", default=None, help="título do catálogo")
    c.add_argument("--subtitulo", default=None, help="linha de apoio na capa")
    c.add_argument("--marca", default=None, help="nome da loja no topo das páginas")
    c.add_argument("--logo", default=None, help="PNG com transparência, no lugar do nome")
    c.add_argument("--chamada", default=None,
                   help="texto grande da página (padrão: PROMOÇÕES)")
    c.add_argument("--capa", default=None, help="imagem de produto para a capa")
    c.add_argument("--sem-capa", action="store_true", help="não gera a capa")
    c.add_argument("--contracapa", default=None, help="texto da última página")
    c.add_argument("--um-arquivo", action="store_true",
                   help="gera um único .html com tudo embutido")
    c.add_argument("--sem-pdf", action="store_true", help="não gera o PDF")
    c.add_argument("--selo-desconto", action="store_true",
                   help="carimba o percentual de desconto em cada página")
    c.add_argument("--rotulo-link", default="Comprar",
                   help="texto do botão nas páginas que têm link")
    c.add_argument("--qualidade", type=int, default=88,
                   help="qualidade do JPEG das páginas desenhadas (1-95)")
    c.add_argument("--recodificar", action="store_true",
                   help="recomprime as artes prontas em JPEG; sem esta flag elas "
                        "entram no catálogo exatamente como estão")
    c.add_argument("--tamanho", default=None, metavar="LxA",
                   help="força o tamanho da página (padrão: o tamanho das próprias artes)")
    c.add_argument("--pdf-largura", type=int, default=1400, metavar="PX",
                   help="largura das páginas dentro do PDF; 0 mantém a resolução cheia")
    c.add_argument("--limite", type=int, default=None, help="usa no máximo N ofertas")
    c.add_argument("--fotos", type=Path, default=None,
                   help="pasta base das imagens citadas na planilha")
    c.add_argument("--produtos", action="store_true",
                   help="a entrada é a planilha de produtos do envio, não de ofertas")
    c.add_argument("--todos", action="store_true",
                   help="com --produtos, inclui também quem não tem preço promocional")
    c.add_argument("--tema", type=Path, default=None, help="outro config/catalogo.yaml")
    c.add_argument("--mapeamento", type=Path, default=None)
    c.set_defaults(func=cmd_catalogo)

    s = sub.add_parser("estado", help="mostra o que já foi enviado desta máquina")
    s.add_argument("--estado", default="estado.sqlite3")
    s.add_argument("--limite", type=int, default=50)
    s.add_argument("--esquecer", metavar="SKU",
                   help="remove um SKU do estado para forçar o reenvio")
    s.set_defaults(func=cmd_estado)

    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    _configurar_log(args.verboso)
    try:
        return int(args.func(args))
    except (ConfigInvalida, PlanilhaInvalida, MagazordError, ValueError,
            FileNotFoundError, NotADirectoryError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrompido pelo usuário.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
