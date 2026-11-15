"""Cortaí: site de cortes virais. API + páginas estáticas no mesmo serviço."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, fila, mailer
from app.config import Config, carregar
from app.db import Banco, agora

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
ESTATICO = Path(__file__).resolve().parent.parent / "static"
COOKIE = "cortai_s"
RE_URL = re.compile(r"^https?://[^\s]{6,500}$")
# Planos (preço de lançamento). videos_mes = pedidos por mês (link, animação ou clone).
PLANOS = {
    "start": {"nome": "Start", "preco": 19.90, "depois": 29.90, "videos_mes": 15,
              "itens": ["15 vídeos por mês (até 5 cortes cada)", "Legenda, gancho e relatório em todos", "Animação por texto"]},
    "pro": {"nome": "Pro", "preco": 29.90, "depois": 39.90, "videos_mes": 60,
            "itens": ["60 vídeos por mês (até 5 cortes cada)", "Animação por texto e clone de Reels", "Vídeos de até 1 hora", "Kit de post com legenda e hashtags"]},
    "turbo": {"nome": "Turbo", "preco": 59.90, "depois": 79.90, "videos_mes": 200,
              "itens": ["200 vídeos por mês", "Fila prioritária", "Tudo do Pro", "Para quem vende cortes para outros criadores"]},
}


def criar_app(cfg: Config | None = None, iniciar_fila: bool = True) -> FastAPI:
    cfg = cfg or carregar()
    Path(cfg.dados).mkdir(parents=True, exist_ok=True)
    db = Banco(os.path.join(cfg.dados, "cortai.db"))
    app = FastAPI(title="Cortaí", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db
    if iniciar_fila:
        app.state.parar = fila.iniciar(db, cfg)

    def usuario(request: Request, obrigatorio: bool = True) -> dict | None:
        u = auth.usuario_da_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        if not u and obrigatorio:
            raise HTTPException(401, "entre na sua conta")
        return u

    def resumo_conta(u: dict) -> dict:
        assin = auth.assinante(u)
        usados = fila.usados_no_mes(db, u["id"])
        pl = PLANOS.get(u["plano"]) or PLANOS["pro"]
        limite = pl["videos_mes"] if assin else cfg.trabalhos_teste
        return {"email": u["email"], "assinante": assin, "plano": u["plano"] if assin else "teste", "ativo_ate": u["ativo_ate"],
                "usados": usados, "limite": limite, "restam": max(0, limite - usados), "checkout": cfg.checkout_mensal, "planos": planos_publicos()}

    def planos_publicos() -> list[dict]:
        return [{"codigo": k, **v, "checkout": cfg.checkouts.get(k) or "", "lancamento_ate": cfg.lancamento_ate} for k, v in PLANOS.items()]

    @app.get("/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------ login
    @app.post("/api/entrar/codigo")
    async def pedir_codigo(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        cod, motivo = auth.novo_codigo(db, cfg.segredo_sessao, email)
        if motivo == "espere":
            raise HTTPException(429, "acabamos de mandar um código; espere alguns segundos")
        enviado = mailer.codigo(cfg, email, cod)
        out = {"ok": True, "enviado": enviado}
        if cfg.dev:
            out["codigo_dev"] = cod
        return out

    @app.post("/api/entrar")
    async def entrar(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        r = auth.conferir_codigo(db, cfg.segredo_sessao, email, str(b.get("codigo") or ""))
        if r != "ok":
            raise HTTPException(400, {"invalido": "código errado", "expirado": "código vencido; peça outro",
                                      "tentativas": "muitas tentativas; peça outro código"}[r])
        u = auth.usuario_por_email(db, email)
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True, "conta": resumo_conta(u)})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    @app.post("/api/sair")
    def sair(request: Request):
        auth.encerrar_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/planos")
    def planos():
        return {"planos": planos_publicos()}

    @app.get("/api/conta")
    def conta(request: Request):
        return resumo_conta(usuario(request))

    # ------------------------------------------------ trabalhos
    def publico(t: dict, cfg=cfg) -> dict:
        res = json.loads(t["resultado"]) if t.get("resultado") else None
        entrada = json.loads(t["entrada"])
        apagado = bool(entrada.get("apagado"))
        if res:
            for c in res.get("cortes", []):
                c["url"] = None if apagado else f"/api/trabalhos/{t['id']}/arquivo/{c['arquivo']}"
                c["capa_url"] = None if apagado or not c.get("capa") else f"/api/trabalhos/{t['id']}/arquivo/{c['capa']}"
        return {"id": t["id"], "tipo": t["tipo"], "estado": t["estado"], "etapa": t["etapa"], "progresso": t["progresso"],
                "entrada": {"url": entrada.get("url"), "texto": entrada.get("texto")}, "erro": t["erro"], "criado": t["criado"], "fim": t["fim"],
                "resultado": res, "arquivos_apagados": apagado, "posicao": fila.posicao(db, t["id"]) if t["estado"] == "fila" else 0}

    @app.post("/api/trabalhos")
    async def novo(request: Request):
        u = usuario(request)
        b = await request.json()
        tipo = b.get("tipo") or "cortes"
        if tipo not in ("cortes", "animacao", "clonar"):
            raise HTTPException(422, "esse tipo chega em breve")
        url = str(b.get("url") or "").strip()
        texto = str(b.get("texto") or "").strip()[:1500]
        if tipo in ("cortes", "clonar") and not RE_URL.match(url):
            raise HTTPException(422, "cole o link completo do vídeo (começando com https://)")
        if tipo in ("animacao", "clonar") and len(texto) < 8:
            raise HTTPException(422, "conte em uma ou duas frases o vídeo que você quer")
        c = resumo_conta(u)
        if c["restam"] <= 0:
            raise HTTPException(402, "escolha um plano para começar a criar" if not c["assinante"] else "você usou todos os vídeos do seu plano neste mês; suba de plano para continuar")
        if tipo == "clonar" and c["plano"] == "start":
            raise HTTPException(402, "clonar Reels faz parte dos planos Pro e Turbo")
        abertos = db.um("SELECT COUNT(*) n FROM trabalhos WHERE usuario=? AND estado IN ('fila','rodando')", (u["id"],))["n"]
        if abertos >= 2:
            raise HTTPException(429, "espere um dos seus pedidos terminar para mandar outro")
        if tipo == "clonar":
            entrada = {"url": url, "texto": texto, "voz": "masculina" if b.get("voz") == "masculina" else "feminina"}
        elif tipo == "animacao":
            entrada = {"texto": texto, "segundos": max(15, min(60, int(b.get("segundos") or 30))), "voz": "masculina" if b.get("voz") == "masculina" else "feminina"}
        else:
            entrada = {"url": url, "quantos": max(1, min(cfg.max_cortes, int(b.get("quantos") or 3)))}
        tid = fila.criar(db, u["id"], tipo, entrada)
        return publico(db.um("SELECT * FROM trabalhos WHERE id=?", (tid,)))

    @app.get("/api/trabalhos")
    def listar(request: Request):
        u = usuario(request)
        return {"trabalhos": [publico(t) for t in db.todos("SELECT * FROM trabalhos WHERE usuario=? ORDER BY criado DESC LIMIT 50", (u["id"],))]}

    @app.get("/api/trabalhos/{tid}")
    def ver(tid: str, request: Request):
        u = usuario(request)
        t = db.um("SELECT * FROM trabalhos WHERE id=? AND usuario=?", (tid, u["id"]))
        if not t:
            raise HTTPException(404, "não encontrado")
        return publico(t)

    @app.get("/api/trabalhos/{tid}/arquivo/{nome}")
    def arquivo(tid: str, nome: str, request: Request, baixar: int = 0):
        u = usuario(request)
        if not re.fullmatch(r"corte\d+\.(mp4|jpg)", nome) or not db.um("SELECT 1 FROM trabalhos WHERE id=? AND usuario=?", (tid, u["id"])):
            raise HTTPException(404, "não encontrado")
        p = fila.pasta_trabalho(cfg, tid) / nome
        if not p.exists():
            raise HTTPException(410, "esse arquivo já foi apagado (guardamos por 7 dias)")
        mt = "video/mp4" if nome.endswith(".mp4") else "image/jpeg"
        return FileResponse(p, media_type=mt, filename=f"cortai-{tid[:6]}-{nome}" if baixar else None)

    # ------------------------------------------------ Cakto (assinatura)
    @app.post("/api/cakto")
    async def cakto(request: Request):
        cru = await request.body()
        try:
            b = json.loads(cru)
        except ValueError:
            raise HTTPException(400, "json")
        if not cfg.cakto_segredo or not hmac.compare_digest(str(b.get("secret") or ""), cfg.cakto_segredo):
            raise HTTPException(401, "segredo")
        evento = str(b.get("event") or "")
        dados = b.get("data") or {}
        if isinstance(dados, list):
            dados = dados[0] if dados else {}
        eid = str(dados.get("id") or hashlib.sha256(cru).hexdigest()[:24]) + ":" + evento
        if db.um("SELECT 1 FROM eventos_cakto WHERE id=?", (eid,)):
            return {"ok": True, "repetido": True}
        db.exec("INSERT INTO eventos_cakto(id,recebido,corpo) VALUES(?,?,?)", (eid, agora(), cru.decode("utf-8", "replace")[:20000]))
        email = auth.normalizar_email((dados.get("customer") or {}).get("email"))
        if not email:
            return {"ok": True, "ignorado": "sem email"}
        u = auth.usuario_por_email(db, email)
        if evento in ("purchase_approved", "subscription_renewed", "subscription_created"):
            base = max(agora(), u["ativo_ate"])
            plano = cfg.planos_por_produto.get(str((dados.get("product") or {}).get("id") or ""), "pro")
            db.exec("UPDATE usuarios SET plano=?, ativo_ate=?, pedido=? WHERE id=?", (plano, base + 32 * 86400, str(dados.get("id") or ""), u["id"]))
            return {"ok": True, "liberado": email}
        if evento in ("refund", "chargeback", "subscription_canceled", "subscription_cancelled"):
            db.exec("UPDATE usuarios SET ativo_ate=? WHERE id=?", (agora(), u["id"]))
            return {"ok": True, "revogado": email}
        return {"ok": True, "ignorado": evento}


    # ------------------------------------------------ admin (painel do ecossistema no llm-access)
    def exigir_admin(request: Request):
        chave = request.headers.get("x-admin-key") or ""
        if not cfg.admin_key or not hmac.compare_digest(chave, cfg.admin_key):
            raise HTTPException(401, "chave de admin")

    @app.get("/api/admin/resumo")
    def admin_resumo(request: Request):
        exigir_admin(request)
        ativos = db.todos("SELECT plano, COUNT(*) n FROM usuarios WHERE ativo_ate>? GROUP BY plano", (agora(),))
        receita = sum(r["n"] * (PLANOS.get(r["plano"]) or PLANOS["pro"])["preco"] for r in ativos)
        mes = agora() - 30 * 86400
        return {"produto": "cortai", "nome": "Cortaí", "url": cfg.base_url,
                "assinantes": sum(r["n"] for r in ativos), "por_plano": {r["plano"]: r["n"] for r in ativos},
                "receita_mensal": round(receita, 2), "cadastros": db.um("SELECT COUNT(*) n FROM usuarios")["n"],
                "pedidos_30d": db.um("SELECT COUNT(*) n FROM trabalhos WHERE criado>?", (mes,))["n"],
                "erros_30d": db.um("SELECT COUNT(*) n FROM trabalhos WHERE criado>? AND estado='erro'", (mes,))["n"],
                "na_fila": db.um("SELECT COUNT(*) n FROM trabalhos WHERE estado IN ('fila','rodando')")["n"],
                "planos": planos_publicos()}

    @app.get("/api/admin/assinantes")
    def admin_assinantes(request: Request):
        exigir_admin(request)
        mes = agora() - 30 * 86400
        linhas = db.todos("""SELECT u.id, u.email, u.criado, u.plano, u.ativo_ate, u.pedido,
                                    (SELECT COUNT(*) FROM trabalhos t WHERE t.usuario=u.id AND t.criado>?) pedidos_30d,
                                    (SELECT MAX(criado) FROM trabalhos t WHERE t.usuario=u.id) ultimo
                             FROM usuarios u ORDER BY u.ativo_ate DESC, u.criado DESC LIMIT 1000""", (mes,))
        for l in linhas:
            l["ativo"] = l["ativo_ate"] > agora()
        return {"assinantes": linhas}

    # ------------------------------------------------ páginas
    @app.get("/", response_class=HTMLResponse)
    def inicio():
        return FileResponse(ESTATICO / "index.html")

    @app.get("/painel", response_class=HTMLResponse)
    def painel(request: Request):
        if not usuario(request, obrigatorio=False):
            return RedirectResponse("/?entrar=1", status_code=303)
        return FileResponse(ESTATICO / "painel.html")

    app.mount("/s", StaticFiles(directory=ESTATICO), name="estatico")
    return app


app = criar_app() if os.environ.get("CORTAI_SEM_APP") != "1" else None
