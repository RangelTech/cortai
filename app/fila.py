"""Fila de trabalhos: threads que pegam o próximo pedido em 'fila' e rodam o pipeline. Limpeza de arquivos antigos."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path

from app import mailer, pipeline
from app.config import Config
from app.db import Banco, agora

log = logging.getLogger("cortai.fila")


def pasta_trabalho(cfg: Config, tid: str) -> Path:
    return Path(cfg.dados) / "trabalhos" / tid


def criar(db: Banco, usuario: int, tipo: str, entrada: dict) -> str:
    tid = uuid.uuid4().hex[:16]
    db.exec("INSERT INTO trabalhos(id,usuario,tipo,entrada,criado) VALUES(?,?,?,?,?)", (tid, usuario, tipo, json.dumps(entrada, ensure_ascii=False), agora()))
    return tid


def posicao(db: Banco, tid: str) -> int:
    t = db.um("""SELECT t.criado, (u.plano='turbo' AND u.ativo_ate>strftime('%s','now')) turbo FROM trabalhos t
                 LEFT JOIN usuarios u ON u.id=t.usuario WHERE t.id=?""", (tid,))
    if not t:
        return 0
    if t["turbo"]:
        return db.um("""SELECT COUNT(*) n FROM trabalhos t JOIN usuarios u ON u.id=t.usuario WHERE t.estado='fila' AND t.criado<?
                        AND u.plano='turbo' AND u.ativo_ate>strftime('%s','now')""", (t["criado"],))["n"]
    return db.um("""SELECT COUNT(*) n FROM trabalhos t LEFT JOIN usuarios u ON u.id=t.usuario WHERE t.estado='fila'
                    AND (t.criado<? OR (u.plano='turbo' AND u.ativo_ate>strftime('%s','now')))""", (t["criado"],))["n"]


def _pegar(db: Banco) -> dict | None:
    with db.tx() as c:
        # fila prioritária do plano Turbo (assinatura ativa): passa na frente; dentro de cada grupo, ordem de chegada
        r = c.execute("""SELECT t.* FROM trabalhos t LEFT JOIN usuarios u ON u.id=t.usuario WHERE t.estado='fila'
                         ORDER BY CASE WHEN u.plano='turbo' AND u.ativo_ate>strftime('%s','now') THEN 0 ELSE 1 END, t.criado LIMIT 1""").fetchone()
        if not r:
            return None
        c.execute("UPDATE trabalhos SET estado='rodando', etapa='Começando', progresso=0.01 WHERE id=?", (r["id"],))
        return dict(r)


def _rodar(db: Banco, cfg: Config, t: dict) -> None:
    tid = t["id"]
    entrada = json.loads(t["entrada"])
    pasta = pasta_trabalho(cfg, tid)

    def avisar(etapa: str, prog: float) -> None:
        db.exec("UPDATE trabalhos SET etapa=?, progresso=? WHERE id=?", (etapa, round(prog, 3), tid))

    try:
        if pipeline.disco_livre(cfg.dados) < 3 * 1024**3:
            raise pipeline.ErroUsuario("Estamos com muitos pedidos agora. Tente de novo em alguns minutos.")
        if t["tipo"] == "cortes":
            res = pipeline.cortes_de_link(entrada["url"], pasta, cfg, avisar, int(entrada.get("quantos") or 3), entrada.get("perfil"))
        elif t["tipo"] == "clonar":
            from app import clonar
            res = clonar.clonar_reels(entrada["url"], entrada["texto"], pasta, cfg, avisar, entrada.get("voz") or "feminina")
            _marcar(res, pasta, entrada.get("perfil"))
        elif t["tipo"] == "animacao":
            from app import animacao
            res = animacao.animacao_de_texto(entrada["texto"], pasta, cfg, avisar, int(entrada.get("segundos") or 30), entrada.get("voz") or "feminina")
            _marcar(res, pasta, entrada.get("perfil"))
        else:
            raise pipeline.ErroUsuario("Esse tipo de pedido ainda não está disponível.")
        db.exec("UPDATE trabalhos SET estado='pronto', etapa='Pronto', progresso=1, resultado=?, fim=? WHERE id=?",
                (json.dumps(res, ensure_ascii=False), agora(), tid))
        u = db.um("SELECT email FROM usuarios WHERE id=?", (t["usuario"],))
        if u and res.get("cortes"):
            mailer.pronto(cfg, u["email"], len(res["cortes"]), res["video"]["titulo"][:80], t["tipo"])
    except pipeline.ErroUsuario as e:
        db.exec("UPDATE trabalhos SET estado='erro', etapa='Não deu certo', erro=?, fim=? WHERE id=?", (str(e), agora(), tid))
        pipeline.limpar_pasta(pasta)
    except Exception as e:  # noqa: BLE001
        log.exception("trabalho %s falhou", tid)
        db.exec("UPDATE trabalhos SET estado='erro', etapa='Não deu certo', erro=?, fim=? WHERE id=?",
                ("Algo deu errado do nosso lado. Esse pedido não conta no seu limite; tente de novo.", agora(), tid))
        db.exec("UPDATE trabalhos SET entrada=json_set(entrada,'$.falha_tecnica',?) WHERE id=?", (str(e)[:500], tid))
        pipeline.limpar_pasta(pasta)


def _marcar(res: dict, pasta: Path, perfil: dict | None) -> None:
    """Animação e clone: uma versão por rede com o @ do cliente (o texto já vem queimado no vídeo)."""
    if not perfil:
        return
    from app import estilo
    for k, c in enumerate(res.get("cortes", []), 1):
        try:
            c["arquivos"] = estilo.marca_simples(pasta / c["arquivo"], pasta, f"corte{k}", perfil)
            c["arquivo"] = next(iter(c["arquivos"].values()))
        except Exception:  # noqa: BLE001  sem o @ o vídeo continua valendo
            log.exception("marca falhou")


def _laco(db: Banco, cfg: Config, parar: threading.Event) -> None:
    while not parar.is_set():
        t = _pegar(db)
        if not t:
            parar.wait(2)
            continue
        _rodar(db, cfg, t)


def limpar_antigos(db: Banco, cfg: Config) -> int:
    """Apaga arquivos de trabalhos com mais de N dias (o relatório fica no banco; os vídeos somem)."""
    limite = agora() - cfg.guardar_dias * 86400
    n = 0
    demo = (cfg.demo_email or "").lower()
    for t in db.todos("""SELECT t.id FROM trabalhos t JOIN usuarios u ON u.id=t.usuario WHERE t.fim IS NOT NULL AND t.fim<?
                         AND (t.entrada NOT LIKE '%\"apagado\"%') AND u.email<>?""", (limite, demo)):  # exemplos da demonstração não somem
        pipeline.limpar_pasta(pasta_trabalho(cfg, t["id"]))
        db.exec("UPDATE trabalhos SET entrada=json_set(entrada,'$.apagado',1) WHERE id=?", (t["id"],))
        n += 1
    return n


def iniciar(db: Banco, cfg: Config) -> threading.Event:
    # pedido que estava rodando quando o serviço caiu volta para a fila
    db.exec("UPDATE trabalhos SET estado='fila', etapa='', progresso=0 WHERE estado='rodando'")
    parar = threading.Event()
    for i in range(max(1, cfg.workers)):
        threading.Thread(target=_laco, args=(db, cfg, parar), name=f"fila-{i}", daemon=True).start()

    def faxina():
        while not parar.wait(3600):
            try:
                limpar_antigos(db, cfg)
            except Exception:  # noqa: BLE001
                log.exception("faxina falhou")
    threading.Thread(target=faxina, name="faxina", daemon=True).start()
    return parar


def usados_no_mes(db: Banco, usuario: int) -> int:
    inicio = time.time() - 30 * 86400
    return db.um("SELECT COUNT(*) n FROM trabalhos WHERE usuario=? AND criado>? AND NOT (estado='erro' AND entrada LIKE '%falha_tecnica%')",
                 (usuario, inicio))["n"]
