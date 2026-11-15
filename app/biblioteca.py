"""Biblioteca de cortes prontos (todas as contas): 10 animações narradas de nichos diferentes, geradas uma vez pelo próprio Cortaí.
Conteúdo nosso (roteiro, voz e imagens geradas): pode postar sem risco de direito autoral. Fica em /data/biblioteca e não é apagado."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.config import Config

log = logging.getLogger("cortai.biblioteca")
TEMAS = [
    ("futebol", "Futebol", "3 curiosidades sobre a Copa do Mundo que quase ninguém sabe, estilo canal de curiosidades de futebol, termina pedindo para seguir", "masculina"),
    ("financas", "Finanças", "3 hábitos de dinheiro que deixam qualquer pessoa mais rica em 1 ano, tom direto e motivador, termina pedindo para salvar", "feminina"),
    ("treino", "Treino em casa", "Treino de 7 minutos em casa sem equipamento: os 4 exercícios e por que funcionam, tom animado", "masculina"),
    ("receita", "Receitas", "Receita de brigadeiro de colher que vende muito: ingredientes e o segredo do ponto, tom acolhedor, termina pedindo para comentar", "feminina"),
    ("ia", "Tecnologia e IA", "3 jeitos de usar inteligência artificial para ganhar tempo no trabalho, tom de dica rápida", "masculina"),
    ("espaco", "Curiosidades", "Por que não existe som no espaço e o que os astronautas ouvem, estilo canal dark de curiosidades", "masculina"),
    ("motivacao", "Motivação", "A regra dos 2 minutos para vencer a procrastinação, tom inspirador e firme, termina pedindo para mandar para um amigo", "feminina"),
    ("historia", "História", "A história curiosa de como o Brasil quase teve outra capital, estilo documentário curto", "masculina"),
    ("games", "Games", "3 segredos escondidos em jogos famosos que quase ninguém descobriu, tom empolgado de canal gamer", "masculina"),
    ("psicologia", "Psicologia", "3 sinais de que uma pessoa gosta de você segundo a psicologia, tom leve e curioso, termina pedindo para comentar", "feminina"),
]


def pasta(cfg: Config) -> Path:
    p = Path(cfg.dados) / "biblioteca"
    p.mkdir(parents=True, exist_ok=True)
    return p


def listar(cfg: Config) -> list[dict]:
    out = []
    for cod, nicho, _, _ in TEMAS:
        j = pasta(cfg) / f"{cod}.json"
        if j.exists():
            try:
                out.append(json.loads(j.read_text(encoding="utf-8")))
            except ValueError:
                pass
    return out


def gerar(cfg: Config, refazer: bool = False) -> int:
    """Gera os que faltam (um por vez). Devolve quantos foram feitos."""
    from app import animacao
    feitos = 0
    for cod, nicho, pedido, voz in TEMAS:
        destino = pasta(cfg) / f"{cod}.json"
        if destino.exists() and not refazer:
            continue
        trab = pasta(cfg) / f"_{cod}"
        try:
            res = animacao.animacao_de_texto(pedido, trab, cfg, lambda e, p: None, 30, voz)
            c = res["cortes"][0]
            (trab / c["arquivo"]).replace(pasta(cfg) / f"{cod}.mp4")
            if c.get("capa") and (trab / c["capa"]).exists():
                (trab / c["capa"]).replace(pasta(cfg) / f"{cod}.jpg")
            destino.write_text(json.dumps({"codigo": cod, "nicho": nicho, "titulo": c["titulo"], "duracao": c["duracao"], "legenda": c.get("legenda", ""),
                                           "hashtags": c.get("hashtags", []), "gancho": c.get("gancho", "")}, ensure_ascii=False), encoding="utf-8")
            feitos += 1
            log.info("biblioteca: %s pronto", cod)
        except Exception:  # noqa: BLE001
            log.exception("biblioteca: %s falhou", cod)
        finally:
            for f in trab.glob("*"):
                f.unlink(missing_ok=True)
            try:
                trab.rmdir()
            except OSError:
                pass
    return feitos


def gerar_em_segundo_plano(cfg: Config, refazer: bool = False) -> None:
    threading.Thread(target=gerar, args=(cfg, refazer), daemon=True, name="biblioteca").start()
