"""Testes do Cortaí: login por código, cota, trabalhos, webhook da Cakto e as partes puras do pipeline."""
import json
import os

os.environ["CORTAI_SEM_APP"] = "1"

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.config import Config
from app.main import criar_app


@pytest.fixture()
def cli(tmp_path):
    cfg = Config(dados=str(tmp_path), dev=True, segredo_sessao="s", cakto_segredo="segredo-cakto", checkout_mensal="https://pay.cakto/x",
                 planos_por_produto={"prod-start": "start", "prod-turbo": "turbo"}, checkouts={"start": "https://pay/s"})
    app = criar_app(cfg, iniciar_fila=False)
    return TestClient(app), app


def assinar(c, email="ana@exemplo.com", produto="prod-start"):
    r = c.post("/api/cakto", json={"secret": "segredo-cakto", "event": "purchase_approved", "data": {"id": "p-" + email + produto, "customer": {"email": email}, "product": {"id": produto}}})
    assert r.status_code == 200


def entrar(c, email="ana@exemplo.com", perfil=True):
    r = c.post("/api/entrar/codigo", json={"email": email})
    assert r.status_code == 200
    r = c.post("/api/entrar", json={"email": email, "codigo": r.json()["codigo_dev"]})
    assert r.status_code == 200
    if perfil:
        assert c.post("/api/perfil", json={"arroba": "minhaloja", "redes": ["instagram", "tiktok"], "cor": "#22aaff"}).status_code == 200
    return r


def test_login_codigo_e_sessao(cli):
    c, _ = cli
    assert c.get("/api/conta").status_code == 401
    entrar(c)
    j = c.get("/api/conta").json()
    assert j["email"] == "ana@exemplo.com" and j["assinante"] is False and j["restam"] == 0
    assert [p["preco"] for p in j["planos"]] == [19.90, 29.90, 59.90] and j["planos"][0]["checkout"] == "https://pay/s"


def test_codigo_errado_e_tentativas(cli):
    c, _ = cli
    c.post("/api/entrar/codigo", json={"email": "b@exemplo.com"})
    for _ in range(5):
        assert c.post("/api/entrar", json={"email": "b@exemplo.com", "codigo": "000000x"}).status_code == 400
    r = c.post("/api/entrar", json={"email": "b@exemplo.com", "codigo": "123456"})
    assert r.status_code == 400 and "tentativas" in r.json()["detail"]


def test_reenvio_rapido_bloqueado(cli):
    c, _ = cli
    assert c.post("/api/entrar/codigo", json={"email": "c@exemplo.com"}).status_code == 200
    assert c.post("/api/entrar/codigo", json={"email": "c@exemplo.com"}).status_code == 429


def test_email_invalido(cli):
    c, _ = cli
    assert c.post("/api/entrar/codigo", json={"email": "nao-e-email"}).status_code == 422


def test_sem_plano_nao_cria_e_start_libera(cli):
    c, _ = cli
    entrar(c)
    assert c.post("/api/trabalhos", json={"url": "isso nao e link"}).status_code == 422
    assert c.post("/api/trabalhos", json={"url": "https://www.youtube.com/watch?v=abc123"}).status_code == 402
    assinar(c)
    j = c.get("/api/conta").json()
    assert j["plano"] == "start" and j["limite"] == 15
    r = c.post("/api/trabalhos", json={"url": "https://www.youtube.com/watch?v=abc123", "quantos": 3})
    assert r.status_code == 200 and r.json()["estado"] == "fila"
    r = c.post("/api/trabalhos", json={"tipo": "animacao", "texto": "curiosidade sobre o polvo"})
    assert r.status_code == 200 and r.json()["tipo"] == "animacao"
    assert c.post("/api/trabalhos", json={"url": "https://youtu.be/xyz"}).status_code == 429  # 2 abertos por vez
    assert len(c.get("/api/trabalhos").json()["trabalhos"]) == 2


def test_trabalho_de_outro_usuario_invisivel(cli):
    c, app = cli
    assinar(c)
    entrar(c)
    tid = c.post("/api/trabalhos", json={"url": "https://youtu.be/abc123"}).json()["id"]
    c2 = TestClient(app)
    entrar(c2, "outro@exemplo.com")
    assert c2.get(f"/api/trabalhos/{tid}").status_code == 404
    assert c2.get(f"/api/trabalhos/{tid}/arquivo/corte1.mp4").status_code == 404
    assert c.get(f"/api/trabalhos/{tid}/arquivo/../../cortai.db").status_code in (404, 422)


def test_cakto_libera_e_revoga(cli):
    c, _ = cli
    corpo = {"secret": "segredo-cakto", "event": "purchase_approved", "data": {"id": "p1", "customer": {"email": "Ana@Exemplo.com"}}}
    assert c.post("/api/cakto", json={**corpo, "secret": "errado"}).status_code == 401
    assert c.post("/api/cakto", json=corpo).json()["liberado"] == "ana@exemplo.com"
    assert c.post("/api/cakto", json=corpo).json()["repetido"] is True
    entrar(c)
    j = c.get("/api/conta").json()
    assert j["assinante"] is True and j["limite"] == 60 and j["plano"] == "pro"  # produto fora do mapa: Pro
    c.post("/api/cakto", json={"secret": "segredo-cakto", "event": "refund", "data": {"id": "p1", "customer": {"email": "ana@exemplo.com"}}})
    assert c.get("/api/conta").json()["assinante"] is False


def test_painel_redireciona_sem_login(cli):
    c, _ = cli
    r = c.get("/painel", follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/").status_code == 200


SEGS = [{"start": float(i * 5), "end": float(i * 5 + 4.8), "text": f"frase numero {i} com varias palavras para contar"} for i in range(40)]


def test_validar_cortes_encaixa_e_sem_sobrepor():
    cortes = [
        {"inicio": 9.7, "fim": 41.0, "titulo": "A", "nota": 90, "hashtags": "#um #dois"},
        {"inicio": 20, "fim": 50, "titulo": "Sobreposto", "nota": 95},
        {"inicio": 100, "fim": 103, "titulo": "Curto demais"},
        {"inicio": 120, "fim": 400, "titulo": "Longo demais", "nota": 50},
        {"titulo": "sem tempo"},
    ]
    ok = pipeline.validar_cortes(cortes, SEGS, 200)
    titulos = [c["titulo"] for c in ok]
    assert "Curto demais" not in titulos and "sem tempo" not in titulos
    assert ok[0]["inicio"] in [s["start"] for s in SEGS]
    for a in ok:
        assert 12 <= a["fim"] - a["inicio"] <= 90
        for b in ok:
            if a is not b:
                assert a["fim"] <= b["inicio"] or a["inicio"] >= b["fim"]
    a = next(c for c in ok if c["titulo"] == "A")
    assert a["hashtags"] == ["#um", "#dois"]


def test_escolha_automatica_sem_sobrepor():
    ok = pipeline.escolha_automatica(SEGS, 200, 3)
    assert len(ok) == 3
    for a in ok:
        assert a["fim"] - a["inicio"] >= 20


def test_legenda_ass_grupos_e_gancho():
    palavras = [{"start": 10 + i * 0.4, "end": 10 + i * 0.4 + 0.35, "word": w} for i, w in enumerate("isso aqui mudou a minha vida, de verdade mesmo.".split())]
    ass = pipeline.legenda_ass(palavras, 10.0, 20.0, "VOCÊ NÃO VAI ACREDITAR")
    assert "Style: Leg,Montserrat ExtraBold" in ass
    assert "Gancho,,0,0,0,,VOCÊ NÃO VAI ACREDITAR" in ass
    falas = [l for l in ass.splitlines() if l.startswith("Dialogue: 0")]
    assert falas and all(len(l.split(",,")[-1].split()) <= 3 for l in falas)
    assert "VIDA" in ass and "0:00:00.00" in falas[0]


def test_json_da_resposta_com_pensamento():
    j = pipeline._json_da_resposta('<think>hmm</think>Aqui: {"resumo":"x","cortes":[]} fim')
    assert j == {"resumo": "x", "cortes": []}


def test_admin_exige_chave_e_resume(cli):
    c = cli[0]
    app = c.app
    app.state.cfg.admin_key = "adm"
    assert c.get("/api/admin/resumo").status_code == 401
    assert c.get("/api/admin/resumo", headers={"x-admin-key": "errada"}).status_code == 401
    r = c.get("/api/admin/resumo", headers={"x-admin-key": "adm"})
    assert r.status_code == 200 and "assinantes" in r.json() and len(r.json()["planos"]) == 3
    assert c.get("/api/admin/assinantes", headers={"x-admin-key": "adm"}).status_code == 200


def test_fila_turbo_passa_na_frente(cli):
    from app import fila
    c, app = cli
    db = app.state.db
    assinar(c, "lento@exemplo.com", "prod-start")
    assinar(c, "rapido@exemplo.com", "prod-turbo")
    lento = db.um("SELECT id FROM usuarios WHERE email='lento@exemplo.com'")["id"]
    rapido = db.um("SELECT id FROM usuarios WHERE email='rapido@exemplo.com'")["id"]
    t1 = fila.criar(db, lento, "cortes", {"url": "https://x/1"})
    t2 = fila.criar(db, rapido, "cortes", {"url": "https://x/2"})
    assert fila.posicao(db, t2) == 0 and fila.posicao(db, t1) == 1
    assert fila._pegar(db)["id"] == t2


def test_senha_so_para_demo(cli):
    c = cli[0]
    cfg = c.app.state.cfg
    cfg.demo_email, cfg.demo_senha = "demo@rangeltech.net", "Demo2026"
    assert c.post("/api/entrar/senha", json={"email": "demo@rangeltech.net", "senha": "errada"}).status_code == 400
    assert c.post("/api/entrar/senha", json={"email": "outro@rangeltech.net", "senha": "Demo2026"}).status_code == 400
    assert c.post("/api/entrar/senha", json={"email": "Demo@RangelTech.net", "senha": "Demo2026"}).status_code == 200
    cfg.demo_senha = ""
    assert c.post("/api/entrar/senha", json={"email": "demo@rangeltech.net", "senha": ""}).status_code == 400


def test_perfil_obrigatorio_antes_do_pedido(cli):
    c, _ = cli
    entrar(c, perfil=False)
    assinar(c)
    r = c.post("/api/trabalhos", json={"url": "https://youtu.be/abc123"})
    assert r.status_code == 428
    assert c.post("/api/perfil", json={"arroba": "", "redes": ["tiktok"]}).status_code == 422
    assert c.post("/api/perfil", json={"arroba": "@minha.loja!", "redes": []}).status_code == 422
    p = c.post("/api/perfil", json={"arroba": "@minha.loja!", "redes": ["tiktok", "orkut"], "cor": "azul"}).json()
    assert p == {"arroba": "minha.loja", "arroba_tiktok": "", "redes": ["tiktok"], "cor": "#ff3d6e"}
    r = c.post("/api/trabalhos", json={"url": "https://youtu.be/abc123"})
    assert r.status_code == 200


def test_versoes_por_rede_e_edicao(cli, tmp_path):
    """Corte limpo de 12 s (vídeo de teste) -> versões TikTok e Instagram com @ e legenda; edição apara e troca o gancho."""
    import json
    import subprocess
    from app import estilo, fila
    c, app = cli
    entrar(c)
    assinar(c)
    db = app.state.db
    u = db.um("SELECT id FROM usuarios WHERE email='ana@exemplo.com'")["id"]
    tid = fila.criar(db, u, "cortes", {"url": "https://x"})
    pasta = fila.pasta_trabalho(app.state.cfg, tid)
    pasta.mkdir(parents=True)
    limpo = pasta / "corte1_limpo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=720x1280:d=12", "-f", "lavfi", "-i", "sine=f=440:d=12",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(limpo)], check=True)
    palavras = [{"start": 1.0 + i * 0.5, "end": 1.4 + i * 0.5, "word": f"palavra{i}"} for i in range(12)]
    arquivos = estilo.versoes(limpo, palavras, "gancho de teste", pasta, "corte1", {"redes": ["tiktok", "instagram"], "arroba": "minhaloja", "cor": "#22aaff"})
    assert set(arquivos) == {"tiktok", "instagram"} and all((pasta / a).exists() for a in arquivos.values())
    res = {"cortes": [{"inicio": 0, "fim": 12, "titulo": "t", "gancho": "GANCHO", "nota": 80, "arquivo": arquivos["tiktok"], "arquivos": arquivos,
                       "limpo": limpo.name, "palavras": palavras, "duracao": 12.0}]}
    db.exec("UPDATE trabalhos SET estado='pronto', resultado=? WHERE id=?", (json.dumps(res), tid))
    pub = c.get(f"/api/trabalhos/{tid}").json()["resultado"]["cortes"][0]
    assert pub["editavel"] and set(pub["urls"]) == {"tiktok", "instagram"} and "palavras" not in pub
    assert c.get(pub["urls"]["tiktok"]).status_code == 200
    r = c.post(f"/api/trabalhos/{tid}/cortes/1", json={"gancho": "novo gancho", "aparar_inicio": 2, "aparar_fim": 1, "legenda": True})
    assert r.status_code == 200, r.text
    novo = r.json()["resultado"]["cortes"][0]
    assert novo["gancho"] == "NOVO GANCHO" and abs(novo["duracao"] - 9.0) < 0.6 and "_ed_" in novo["arquivo"]
    assert c.post(f"/api/trabalhos/{tid}/cortes/1", json={"aparar_inicio": 8, "aparar_fim": 3}).status_code == 422


def test_trechos_falados_tiram_pausas_e_muletas():
    from app import falado
    pal = [{"start": 0.5, "end": 0.9, "word": "Oi"}, {"start": 1.0, "end": 1.4, "word": "gente"},
           {"start": 3.5, "end": 3.8, "word": "éé"},                       # muleta sozinha: sai
           {"start": 5.0, "end": 5.5, "word": "hoje"}, {"start": 5.6, "end": 6.0, "word": "vamos"}]
    tr = falado.trechos_falados(pal, 8.0)
    assert len(tr) == 2 and tr[0][0] == 0.4 and tr[1][0] == 4.9
    novas = falado.remapear(pal, tr)
    assert [w["word"] for w in novas] == ["Oi", "gente", "hoje", "vamos"]
    assert novas[2]["start"] < 1.4 and novas[-1]["end"] <= sum(b - a for a, b in tr) + 0.01


def test_video_falado_ponta_a_ponta(cli, monkeypatch):
    """Sobe um vídeo de 10 s com 4 s de silêncio no meio: sai mais curto, com versão TikTok e Instagram e o @ do perfil."""
    import subprocess
    from pathlib import Path
    from app import falado, fila
    c, app = cli
    entrar(c); assinar(c)
    tmp = Path(app.state.cfg.dados) / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:d=10", "-f", "lavfi", "-i", "sine=f=300:d=10",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(tmp)], check=True)
    r = c.post("/api/falado", content=tmp.read_bytes(), headers={"x-arquivo": "meu video.mp4"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    palavras = [{"start": 0.3 + i * 0.4, "end": 0.6 + i * 0.4, "word": f"p{i}"} for i in range(8)] + \
               [{"start": 7.0 + i * 0.4, "end": 7.3 + i * 0.4, "word": f"q{i}"} for i in range(6)]
    monkeypatch.setattr(falado, "transcrever", lambda audio, cfg, cli_: {"segments": [{"start": 0, "end": 10, "text": "x"}], "words": palavras})
    monkeypatch.setattr(falado, "textos", lambda fala, cfg, cli_: {"titulo": "Meu teste", "gancho": "olha isso", "legenda": "comenta", "hashtags": ["#a"]})
    t = app.state.db.um("SELECT * FROM trabalhos WHERE id=?", (tid,))
    fila._rodar(app.state.db, app.state.cfg, t)
    t = c.get(f"/api/trabalhos/{tid}").json()
    assert t["estado"] == "pronto", t
    corte = t["resultado"]["cortes"][0]
    assert set(corte["urls"]) == {"instagram", "tiktok"} and corte["gancho"] == "OLHA ISSO"
    assert corte["duracao"] < 7.5 and c.get(corte["urls"]["tiktok"]).status_code == 200
    assert not list((Path(app.state.cfg.dados) / "uploads").glob("*"))


def test_live_longa_escolhe_em_janelas(monkeypatch):
    """Transcrição de 4 h (muito texto): a IA é chamada por janela e ficam os cortes de nota maior, sem sobrepor."""
    from app import pipeline
    segs = [{"start": i * 6.0, "end": i * 6.0 + 5.5, "text": f"frase numero {i} " + "bla " * 12} for i in range(2400)]
    chamadas = []

    def falso(segs_jan, segs_, dur, cfg, cli_, n, trocas, picos):
        chamadas.append(len(segs_jan))
        a = segs_jan[len(segs_jan) // 2]["start"]
        return {"resumo": "live", "cortes": [{"inicio": a, "fim": a + 30, "titulo": "t", "nota": 50 + len(chamadas)}]}
    monkeypatch.setattr(pipeline, "_escolher_janela", falso)
    r = pipeline.escolher({"segments": segs, "duration": 14400}, Config(), None, 3)
    assert len(chamadas) > 3 and len(r["cortes"]) == 3
    assert [c["nota"] for c in sorted(r["cortes"], key=lambda c: -c["nota"])][0] == 50 + len(chamadas)


def test_transcricao_em_pedacos_soma_deslocamento(tmp_path, monkeypatch):
    import subprocess
    from app import pipeline
    a = tmp_path / "audio.ogg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=200:d=300", "-ac", "1", "-ar", "16000", "-c:a", "libopus", str(a)], check=True)
    monkeypatch.setattr(pipeline, "PEDACO_S", 60)
    monkeypatch.setattr(pipeline, "_transcrever_um", lambda p, cfg, c: {"segments": [{"start": 1.0, "end": 2.0, "text": "oi"}], "words": [{"start": 1.0, "end": 1.5, "word": "oi"}]})
    j = pipeline.transcrever(a, Config(), None)
    assert len(j["segments"]) == 5 and abs(j["segments"][1]["start"] - 61.0) < 1.5 and abs(j["segments"][2]["start"] - 121.0) < 2.5
    assert not (tmp_path / "pedacos").exists()


def test_picos_de_volume(tmp_path):
    import subprocess
    from app import pipeline
    a = tmp_path / "a.ogg"
    # 120 s baixinho com 2 gritos (volume alto) em 40 s e 90 s
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=300:d=120", "-af",
                    "volume='if(between(t,40,42)+between(t,90,92),1,0.02)':eval=frame", "-ac", "1", "-ar", "16000", "-c:a", "libopus", str(a)], check=True)
    p = pipeline.picos_de_volume(a)
    assert any(39 <= x <= 43 for x in p) and any(89 <= x <= 93 for x in p) and len(p) <= 4


def test_transcricao_tenta_de_novo_em_502(tmp_path, monkeypatch):
    import time
    from app import pipeline
    a = tmp_path / "a.ogg"
    a.write_bytes(b"x" * 100)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    respostas = iter([502, 502, 200])

    class R:
        def __init__(self, st): self.status_code, self.text = st, "erro"
        def json(self): return {"segments": [{"start": 0, "end": 1, "text": "oi"}]}

    class C:
        def post(self, *a, **k): return R(next(respostas))
    assert pipeline._transcrever_um(a, Config(), C())["segments"][0]["text"] == "oi"
