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


def entrar(c, email="ana@exemplo.com"):
    r = c.post("/api/entrar/codigo", json={"email": email})
    assert r.status_code == 200
    r = c.post("/api/entrar", json={"email": email, "codigo": r.json()["codigo_dev"]})
    assert r.status_code == 200
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
