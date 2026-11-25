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

from app import senha_rotas
from produtos_core import acesso, armazenamento
from app import auth, fila, mailer
from app.config import Config, carregar
from app.db import Banco, agora
from app import admin  # painel /admin e prompts editáveis

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
    admin.preparar(db)
    app = FastAPI(title="Cortaí", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db

    def apagar_arquivos_usuario(u: dict) -> int:
        """Apagar usuário no /admin (spec 57 §5.1): pasta de cada pedido e o vídeo que ele enviou, no disco e no MinIO."""
        esp, uploads, n = fila.espelho(cfg), (Path(cfg.dados) / "uploads").resolve(), 0
        for t in db.todos("SELECT id, entrada FROM trabalhos WHERE usuario=?", (u["id"],)):
            esp.apagar_pasta(fila.pasta_trabalho(cfg, t["id"])); n += 1
            try:
                ent = json.loads(t["entrada"] or "{}") or {}
            except ValueError:
                ent = {}
            for chave in ("upload", "arquivo"):
                arq = ent.get(chave)
                if isinstance(arq, str) and arq and Path(arq).resolve().is_relative_to(uploads):
                    esp.apagar(arq); n += 1
        return n

    app.state.apagar_arquivos_usuario = apagar_arquivos_usuario
    if cfg.demo_email:  # conta de demonstração: sempre com o plano Turbo ativo
        u = auth.usuario_por_email(db, cfg.demo_email.lower())
        db.exec("UPDATE usuarios SET plano='turbo', ativo_ate=?, arroba=COALESCE(arroba,'cortaioficial'), redes=COALESCE(redes,'instagram,tiktok'), marca_cor=COALESCE(marca_cor,'#ff3d6e') WHERE id=?",
                (agora() + 3650 * 86400, u["id"]))
    if iniciar_fila:
        app.state.parar = fila.iniciar(db, cfg)

    def usuario(request: Request, obrigatorio: bool = True) -> dict | None:
        u = auth.usuario_da_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        if not u and obrigatorio:
            raise HTTPException(401, "entre na sua conta")
        return u

    def perfil_de(u: dict) -> dict:
        redes = [r for r in (u.get("redes") or "").split(",") if r]
        return {"arroba": u.get("arroba") or "", "arroba_tiktok": u.get("arroba_tiktok") or "", "redes": redes, "cor": u.get("marca_cor") or "#ff3d6e"}

    def resumo_conta(u: dict) -> dict:
        assin = auth.assinante(u)
        usados = fila.usados_no_mes(db, u["id"])
        pl = PLANOS.get(u["plano"]) or PLANOS["pro"]
        limite = pl["videos_mes"] if assin else cfg.trabalhos_teste
        return {"email": u["email"], "assinante": assin, "perfil": perfil_de(u), "perfil_ok": bool((u.get("arroba") or u.get("arroba_tiktok")) and u.get("redes")), "plano": u["plano"] if assin else "teste", "ativo_ate": u["ativo_ate"],
                "usados": usados, "limite": limite, "restam": max(0, limite - usados), "checkout": cfg.checkout_mensal, "planos": planos_publicos()}

    def planos_publicos() -> list[dict]:
        return [{"codigo": k, **v, "checkout": cfg.checkouts.get(k) or "", "lancamento_ate": cfg.lancamento_ate} for k, v in PLANOS.items()]

    @app.get("/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------ login
    def _login(u: dict) -> JSONResponse:
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    acesso.registrar(app, db, enviar=lambda para, assunto, texto, html: mailer.enviar(cfg, para, assunto, texto, html),
                     login=_login, usuario=usuario, criar_usuario=lambda email: auth.usuario_por_email(db, email),
                     produto="Cortaí", base_url=cfg.base_url, destino="/painel", cor="#ff3d6e",
                     demo_email=lambda: cfg.demo_email, demo_senha=lambda: cfg.demo_senha, dev=cfg.dev)  # cadastro e login comuns (spec 57 §5A)
    armazenamento.registrar(app, fila.espelho(cfg).arm)  # MinIO (spec 57 §8.6)

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
                c["urls"] = {} if apagado else {r: f"/api/trabalhos/{t['id']}/arquivo/{a}" for r, a in (c.get("arquivos") or {}).items()}
                c["editavel"] = bool(c.get("limpo")) and not apagado
                c.pop("palavras", None)
                c["capa_url"] = None if apagado or not c.get("capa") else f"/api/trabalhos/{t['id']}/arquivo/{c['capa']}"
            if res.get("compilado"):
                res["compilado_url"] = None if apagado else f"/api/trabalhos/{t['id']}/arquivo/{res['compilado']}"
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
        if not c["perfil_ok"]:
            raise HTTPException(428, "antes do primeiro corte, diga seu @ e para quais redes você vai postar")
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
            from app.ganchos import ESTILOS
            entrada = {"texto": texto, "segundos": max(15, min(60, int(b.get("segundos") or 30))), "voz": "masculina" if b.get("voz") == "masculina" else "feminina",
                       "estilo": b.get("estilo") if b.get("estilo") in ESTILOS else None}
        else:
            entrada = {"url": url, "quantos": max(1, min(cfg.max_cortes, int(b.get("quantos") or 3)))}
        pf = perfil_de(u)
        entrada["perfil"] = {"arroba": pf["arroba"] or pf["arroba_tiktok"], "redes": pf["redes"], "cor": pf["cor"]}
        tl = b.get("tela") if isinstance(b.get("tela"), dict) else None
        if tl and tipo == "cortes":
            from app import tela
            arq = reacao_de(u)
            if not arq:
                raise HTTPException(422, "para a tela dividida, suba antes o seu vídeo ou foto de reação em Minha marca")
            entrada["perfil"]["tela"] = {"arquivo": str(arq), "layout": tl.get("layout") if tl.get("layout") in tela.LAYOUTS else "cima",
                                         "audio_meu": bool(tl.get("audio_meu"))}
        tid = fila.criar(db, u["id"], tipo, entrada)
        return publico(db.um("SELECT * FROM trabalhos WHERE id=?", (tid,)))

    def reacao_de(u: dict):
        d = Path(cfg.dados) / "reacao"
        for f in d.glob(f"{u['id']}.*") if d.exists() else []:
            return f
        return None

    @app.get("/api/reacao")
    def reacao_info(request: Request):
        u = usuario(request)
        f = reacao_de(u)
        if not f:
            return {"tem": False}
        from app import tela
        return {"tem": True, "tipo": "foto" if tela.eh_foto(f) else "video", "url": "/api/reacao/arquivo", "segundos": round(tela.duracao(f), 1) if not tela.eh_foto(f) else None}

    @app.get("/api/reacao/arquivo")
    def reacao_arquivo(request: Request):
        f = reacao_de(usuario(request))
        if not f:
            raise HTTPException(404, "sem reação")
        return FileResponse(f)

    @app.delete("/api/reacao")
    def reacao_apagar(request: Request):
        f = reacao_de(usuario(request))
        if f:
            f.unlink(missing_ok=True)
        return {"ok": True}

    @app.post("/api/reacao")
    async def reacao_subir(request: Request):
        """Vídeo (até 2 min, 200 MB) ou foto do cliente para a tela dividida. Corpo cru, nome no cabeçalho x-arquivo."""
        from app import tela
        u = usuario(request)
        nome = request.headers.get("x-arquivo") or "reacao.mp4"
        ext = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ".mp4"
        if ext not in tela.EXT_VIDEO + tela.EXT_FOTO:
            raise HTTPException(422, "mande um vídeo (MP4, MOV, WEBM) ou uma foto (JPG, PNG)")
        d = Path(cfg.dados) / "reacao"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{u['id']}.novo{ext}"
        total = 0
        with open(tmp, "wb") as f:
            async for parte in request.stream():
                total += len(parte)
                if total > 200 * 1024 * 1024:
                    f.close(); tmp.unlink(missing_ok=True)
                    raise HTTPException(413, "o arquivo passa de 200 MB")
                f.write(parte)
        if total < 1000:
            tmp.unlink(missing_ok=True)
            raise HTTPException(422, "o arquivo chegou vazio; tente de novo")
        if ext in tela.EXT_VIDEO:
            dur = tela.duracao(tmp)
            if dur <= 0:
                tmp.unlink(missing_ok=True)
                raise HTTPException(422, "não consegui ler esse vídeo; exporte em MP4")
            if dur > tela.MAX_REACAO_S:
                tmp.unlink(missing_ok=True)
                raise HTTPException(422, "o vídeo de reação pode ter até 2 minutos")
        for velho in d.glob(f"{u['id']}.*"):
            if velho != tmp:
                velho.unlink(missing_ok=True)
        final = d / f"{u['id']}{ext}"
        tmp.replace(final)
        return {"ok": True, "tipo": "foto" if tela.eh_foto(final) else "video"}

    @app.post("/api/falado")
    async def novo_falado(request: Request):
        """Sobe o vídeo falado (corpo cru, até 400 MB) e cria o pedido. Sem multipart para não depender de biblioteca extra."""
        u = usuario(request)
        c = resumo_conta(u)
        if not c["perfil_ok"]:
            raise HTTPException(428, "antes do primeiro corte, diga seu @ e para quais redes você vai postar")
        if c["restam"] <= 0:
            raise HTTPException(402, "escolha um plano para começar a criar" if not c["assinante"] else "você usou todos os vídeos do seu plano neste mês; suba de plano para continuar")
        if db.um("SELECT COUNT(*) n FROM trabalhos WHERE usuario=? AND estado IN ('fila','rodando')", (u["id"],))["n"] >= 2:
            raise HTTPException(429, "espere um dos seus pedidos terminar para mandar outro")
        nome = request.headers.get("x-arquivo") or "video.mp4"
        ext = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ".mp4"
        if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
            raise HTTPException(422, "mande um vídeo MP4, MOV ou WEBM")
        import uuid as _uuid
        dest_dir = Path(cfg.dados) / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{_uuid.uuid4().hex}{ext}"
        total = 0
        with open(dest, "wb") as f:
            async for parte in request.stream():
                total += len(parte)
                if total > 400 * 1024 * 1024:
                    f.close(); dest.unlink(missing_ok=True)
                    raise HTTPException(413, "o vídeo passa de 400 MB; exporte em 1080p ou menos")
                f.write(parte)
        if total < 10_000:
            dest.unlink(missing_ok=True)
            raise HTTPException(422, "o arquivo chegou vazio; tente de novo")
        fila.espelho(cfg).guardar(dest)  # o vídeo do cliente também fica no MinIO
        pf = perfil_de(u)
        entrada = {"upload": str(dest), "nome": nome[:120], "perfil": {"arroba": pf["arroba"] or pf["arroba_tiktok"], "redes": pf["redes"], "cor": pf["cor"]}}
        tid = fila.criar(db, u["id"], "falado", entrada)
        return publico(db.um("SELECT * FROM trabalhos WHERE id=?", (tid,)))

    @app.get("/api/estilos")
    def estilos():
        from app.ganchos import ESTILOS
        return {"estilos": [{"codigo": k, "nome": v[0]} for k, v in ESTILOS.items()]}

    @app.get("/api/biblioteca")
    def biblioteca_lista(request: Request):
        usuario(request)
        from app import biblioteca
        itens = biblioteca.listar(cfg)
        for i in itens:
            i["url"] = f"/api/biblioteca/{i['codigo']}.mp4"
            i["capa_url"] = f"/api/biblioteca/{i['codigo']}.jpg"
        return {"itens": itens, "total": len(biblioteca.TEMAS)}

    @app.get("/api/biblioteca/{nome}")
    def biblioteca_arquivo(nome: str, request: Request, baixar: int = 0):
        usuario(request)
        from app import biblioteca
        if not re.fullmatch(r"[a-z]+\.(mp4|jpg)", nome):
            raise HTTPException(404, "não encontrado")
        p = fila.espelho(cfg).garantir(biblioteca.pasta(cfg) / nome)
        if not p.exists():
            raise HTTPException(404, "não encontrado")
        return FileResponse(p, media_type="video/mp4" if nome.endswith(".mp4") else "image/jpeg", filename=f"cortai-{nome}" if baixar else None)

    @app.post("/api/admin/biblioteca/gerar")
    def biblioteca_gerar(request: Request, refazer: int = 0):
        exigir_admin(request)
        from app import biblioteca
        biblioteca.gerar_em_segundo_plano(cfg, bool(refazer))
        return {"ok": True, "ja_prontos": len(biblioteca.listar(cfg))}

    @app.get("/api/perfil")
    def perfil_get(request: Request):
        return perfil_de(usuario(request))

    @app.post("/api/perfil")
    async def perfil_set(request: Request):
        u = usuario(request)
        b = await request.json()
        limpa = lambda x: re.sub(r"[^A-Za-z0-9_.]", "", str(x or "").strip().lstrip("@"))[:30]
        arroba, arroba_tt = limpa(b.get("arroba")), limpa(b.get("arroba_tiktok"))
        redes = [r for r in (b.get("redes") or []) if r in ("tiktok", "instagram", "youtube")]
        cor = str(b.get("cor") or "#ff3d6e")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor):
            cor = "#ff3d6e"
        if not (arroba or arroba_tt):
            raise HTTPException(422, "informe seu @ (Instagram ou TikTok)")
        if not redes:
            raise HTTPException(422, "escolha pelo menos uma rede")
        db.exec("UPDATE usuarios SET arroba=?, arroba_tiktok=?, redes=?, marca_cor=? WHERE id=?", (arroba, arroba_tt, ",".join(redes), cor, u["id"]))
        return perfil_de(db.um("SELECT * FROM usuarios WHERE id=?", (u["id"],)))

    @app.post("/api/trabalhos/{tid}/cortes/{n}")
    async def editar_corte(tid: str, n: int, request: Request):
        """Edita um corte pronto a partir do corte limpo: gancho, aparar início/fim, legenda ligada/desligada, cor e @ atuais do perfil."""
        u = usuario(request)
        t = db.um("SELECT * FROM trabalhos WHERE id=? AND usuario=?", (tid, u["id"]))
        if not t or t["estado"] != "pronto":
            raise HTTPException(404, "corte não encontrado")
        res = json.loads(t["resultado"])
        if not (1 <= n <= len(res.get("cortes", []))):
            raise HTTPException(404, "corte não encontrado")
        c = res["cortes"][n - 1]
        pasta = fila.pasta_trabalho(cfg, tid)
        if c.get("limpo"):
            fila.espelho(cfg).garantir(pasta / c["limpo"])
        if not c.get("limpo") or not (pasta / c["limpo"]).exists():
            raise HTTPException(409, "este corte não pode ser editado (animações, clones e cortes antigos saem prontos)")
        b = await request.json()
        from app import estilo
        import subprocess
        limpo = pasta / c["limpo"]
        dur = estilo.duracao(limpo)
        try:
            a = max(0.0, float(b.get("aparar_inicio") or 0))
            z = max(0.0, float(b.get("aparar_fim") or 0))
        except (TypeError, ValueError):
            raise HTTPException(422, "valores de corte inválidos")
        if dur - a - z < 5:
            raise HTTPException(422, "o corte ficaria curto demais (mínimo 5 segundos)")
        fonte, palavras = limpo, c.get("palavras") or []
        if a or z:  # apara a partir do limpo original e realinha as palavras
            fonte = pasta / f"corte{n}_editado_limpo.mp4"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-to", f"{dur - z:.2f}", "-i", str(limpo), "-c:v", "libx264", "-preset", "veryfast",
                                "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(fonte)], capture_output=True, text=True)
            if r.returncode != 0:
                raise HTTPException(500, "não consegui aparar o corte agora")
            palavras = [{**w, "start": round(w["start"] - a, 2), "end": round(w["end"] - a, 2)} for w in palavras if w["end"] > a and w["start"] < dur - z]
        gancho = str(b.get("gancho") if b.get("gancho") is not None else c.get("gancho", ""))[:60]
        legenda = bool(b.get("legenda", True))
        arquivos = estilo.versoes(fonte, palavras, gancho, pasta, f"corte{n}_ed", {**perfil_de(u), "arroba": perfil_de(u)["arroba"] or perfil_de(u)["arroba_tiktok"]}, legenda)
        fila.espelho(cfg).guardar_pasta(pasta)
        c.update({"arquivos": arquivos, "arquivo": next(iter(arquivos.values())), "gancho": gancho.upper(), "legenda_on": legenda,
                  "editado": {"aparar_inicio": a, "aparar_fim": z}, "duracao": round(dur - a - z, 1)})
        db.exec("UPDATE trabalhos SET resultado=? WHERE id=?", (json.dumps(res, ensure_ascii=False), tid))
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
        if not re.fullmatch(r"(corte\d+(_ed)?(_(limpo|tiktok|instagram|youtube))?\.(mp4|jpg)|melhores_momentos\.mp4)", nome) or not db.um("SELECT 1 FROM trabalhos WHERE id=? AND usuario=?", (tid, u["id"])):
            raise HTTPException(404, "não encontrado")
        p = fila.espelho(cfg).garantir(fila.pasta_trabalho(cfg, tid) / nome)
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
        if str((dados.get("product") or {}).get("id") or "") not in cfg.planos_por_produto:  # ex.: produto de exemplo do evento de teste
            return {"ok": True, "ignorado": "produto fora do mapa"}
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

    @app.post("/api/admin/cortesia")
    async def admin_cortesia(request: Request):
        """Libera o Cortaí para um e-mail por N dias (ponte com outros produtos: Perfilaí Turbo inclui o Cortaí Pro)."""
        exigir_admin(request)
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        plano = b.get("plano") if b.get("plano") in PLANOS else "pro"
        dias = max(0, min(400, int(32 if b.get("dias") is None else b.get("dias"))))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        u = auth.usuario_por_email(db, email)
        ate = agora() + dias * 86400 if dias else agora()
        db.exec("UPDATE usuarios SET plano=?, ativo_ate=MAX(ativo_ate, ?) WHERE id=?" if dias else "UPDATE usuarios SET ativo_ate=? WHERE id=?",
                (plano, ate, u["id"]) if dias else (ate, u["id"]))
        return {"ok": True, "email": email, "plano": plano, "ativo_ate": db.um("SELECT ativo_ate FROM usuarios WHERE id=?", (u["id"],))["ativo_ate"]}

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

    app.include_router(admin.rotas(db, cfg.segredo_sessao, """SELECT email, criado, plano, ativo_ate, (ativo_ate > strftime('%s','now')) ativo
        FROM usuarios ORDER BY criado DESC""", seguro=not cfg.dev))

    @app.get("/admin", response_class=HTMLResponse)
    def pagina_admin():
        return FileResponse(ESTATICO / "admin.html")

    app.mount("/s", StaticFiles(directory=ESTATICO), name="estatico")
    return app


app = criar_app() if os.environ.get("CORTAI_SEM_APP") != "1" else None
