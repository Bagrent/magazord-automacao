"""Command line entry point.

    magazord modelo                      gera uma planilha de exemplo
    magazord validar  produtos.xlsx      lê e critica a planilha, sem rede
    magazord enviar   produtos.xlsx      simula o envio (padrão seguro)
    magazord enviar   produtos.xlsx --confirmar    envia de verdade
    magazord estado                      mostra o que já foi enviado
    magazord descobrir                   lê a API da loja e revela o contrato real
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
    from .modelo import gerar

    destino = gerar(Path(args.destino))
    print(f"modelo gerado: {destino}")
    print("Preencha uma linha por SKU. As colunas 'Imagem 1..N' recebem links públicos.")
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


def cmd_descobrir(args: argparse.Namespace) -> int:
    from .descoberta import Descobridor, formatar_relatorio, salvar

    carregar_dotenv()
    base_url = os.environ.get("MAGAZORD_BASE_URL", "").strip()
    usuario = os.environ.get("MAGAZORD_USER", "").strip()
    token = os.environ.get("MAGAZORD_TOKEN", "").strip()
    faltando = [
        nome
        for nome, valor in (
            ("MAGAZORD_BASE_URL", base_url),
            ("MAGAZORD_USER", usuario),
            ("MAGAZORD_TOKEN", token),
        )
        if not valor
    ]
    if faltando:
        raise ConfigInvalida(
            f"{', '.join(faltando)} não definida(s). Copie .env.example para .env "
            "e preencha antes de rodar a descoberta."
        )

    print(f"Sondando {base_url} — apenas leitura, nada é criado ou alterado.\n")
    with Descobridor(base_url, usuario, token) as descobridor:
        resultado = descobridor.executar()

    print(formatar_relatorio(resultado))
    destino = salvar(resultado, Path(args.saida))
    print(f"\nDados brutos salvos em {destino}")
    return 0 if resultado.autenticou else 1


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
    m.add_argument("--destino", default="exemplos/modelo_produtos.xlsx")
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

    s = sub.add_parser("estado", help="mostra o que já foi enviado desta máquina")
    s.add_argument("--estado", default="estado.sqlite3")
    s.add_argument("--limite", type=int, default=50)
    s.add_argument("--esquecer", metavar="SKU",
                   help="remove um SKU do estado para forçar o reenvio")
    s.set_defaults(func=cmd_estado)

    d = sub.add_parser(
        "descobrir",
        help="consulta a API da loja e mostra os caminhos e campos reais (só leitura)",
    )
    d.add_argument("--saida", default="saida",
                   help="pasta onde salvar o JSON da descoberta (padrão: saida)")
    d.set_defaults(func=cmd_descobrir)

    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    _configurar_log(args.verboso)
    try:
        return int(args.func(args))
    except (ConfigInvalida, PlanilhaInvalida, MagazordError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrompido pelo usuário.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
