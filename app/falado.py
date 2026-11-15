"""Meu vídeo falado (spec 42): a pessoa sobe o vídeo dela falando para a câmera; tiramos as pausas e os "éé",
pomos legenda palavra a palavra, gancho, @ e uma versão por rede. Sem escolher trechos: o vídeo inteiro, enxuto."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import httpx

from app.config import Config
from app.ganchos import REPERTORIO
from app.pipeline import ErroUsuario, _json_da_resposta, duracao, extrair_audio, renderizar, transcrever

log = logging.getLogger("cortai.falado")
PAUSA = 0.45        # silêncio entre palavras acima disso sai
FOLGA = 0.10        # respiro mantido antes e depois de cada trecho falado
MULETAS = re.compile(r"^(é+|éé+|ã+|ãh+|hum+|hm+|ahn*|eh+|uh+|tipo assim)[.,!?…]*$", re.I)
MAX_MIN = 10

PROMPT = """Você edita Reels. Abaixo está a fala de um vídeo curto gravado pela própria pessoa.
Escreva em português do Brasil:
- "titulo": título curto (até 60 letras)
- "gancho": texto para o topo do vídeo nos 3 primeiros segundos (até 40 letras, MAIÚSCULAS), fiel ao que é dito
- "legenda": legenda do post com chamada para comentar
- "hashtags": 5 hashtags do nicho
{repertorio}
Responda SÓ com JSON {{"titulo": "", "gancho": "", "legenda": "", "hashtags": []}}.

Fala:
{fala}"""


def trechos_falados(palavras: list[dict], dur: float, pausa: float = PAUSA, folga: float = FOLGA) -> list[tuple[float, float]]:
    """Junta as palavras em trechos contínuos; pausas maiores que `pausa` e muletas sozinhas ficam de fora."""
    boas = [w for w in palavras if str(w.get("word", "")).strip() and not MULETAS.match(str(w["word"]).strip())]
    if not boas:
        return []
    out: list[list[float]] = []
    for w in boas:
        a, b = max(0.0, float(w["start"]) - folga), min(dur, float(w["end"]) + folga)
        if out and a - out[-1][1] <= pausa - 2 * folga:
            out[-1][1] = max(out[-1][1], b)
        elif out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a >= 0.2]


def remapear(palavras: list[dict], trechos: list[tuple[float, float]]) -> list[dict]:
    """Tempos das palavras na linha do tempo nova (sem as pausas)."""
    out, desloc, k = [], 0.0, 0
    acum = []
    for a, b in trechos:
        acum.append((a, b, desloc))
        desloc += b - a
    for w in palavras:
        s, e = float(w["start"]), float(w["end"])
        while k < len(acum) and acum[k][1] < s:
            k += 1
        if k == len(acum):
            break
        a, b, d = acum[k]
        if e <= a or MULETAS.match(str(w.get("word", "")).strip()):
            continue
        out.append({"start": round(max(s, a) - a + d, 2), "end": round(min(e, b) - a + d, 2), "word": w["word"]})
    return out


def cortar_pausas(origem: Path, trechos: list[tuple[float, float]], destino: Path) -> list[tuple[float, float]]:
    while len(trechos) > 400:  # filtro inline: junta os trechos mais próximos para caber na linha de comando
        k = min(range(len(trechos) - 1), key=lambda i: trechos[i + 1][0] - trechos[i][1])
        trechos = trechos[:k] + [(trechos[k][0], trechos[k + 1][1])] + trechos[k + 2:]
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in trechos)
    filtro = f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB[v];[0:a]aselect='{sel}',asetpts=N/SR/TB[a]"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(origem), "-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(destino)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (pausas): " + r.stderr[-400:])
    return trechos


def textos(fala: str, cfg: Config, cliente: httpx.Client) -> dict:
    try:
        r = cliente.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, timeout=120,
                         json={"model": cfg.llm_modelo, "temperature": 0.6, "max_tokens": 700, "chat_template_kwargs": {"enable_thinking": False},
                               "messages": [{"role": "user", "content": PROMPT.format(fala=fala[:6000], repertorio=REPERTORIO)}]})
        r.raise_for_status()
        return _json_da_resposta(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        log.warning("textos do vídeo falado: %s", e)
        return {}


def editar_falado(upload: Path, pasta: Path, cfg: Config, avisar: Callable[[str, float], None], perfil: dict | None = None) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    origem = pasta / ("origem" + (upload.suffix.lower() or ".mp4"))
    shutil.move(str(upload), origem)
    try:
        dur = duracao(origem)
        if dur <= 0:
            raise ErroUsuario("Não consegui abrir esse arquivo. Mande um vídeo MP4, MOV ou WEBM.")
        if dur > MAX_MIN * 60:
            raise ErroUsuario(f"O vídeo tem {round(dur / 60)} minutos. Para o vídeo falado o limite é {MAX_MIN} minutos.")
        with httpx.Client() as cliente:
            avisar("Ouvindo a sua fala", 0.15)
            audio = extrair_audio(str(origem), pasta)
            trans = transcrever(audio, cfg, cliente)
            audio.unlink(missing_ok=True)
            palavras = trans.get("words") or []
            if len(palavras) < 8:
                raise ErroUsuario("Quase não tem fala nesse vídeo. Esta opção é para vídeo de alguém falando para a câmera.")
            avisar("Tirando as pausas", 0.4)
            trechos = trechos_falados(palavras, dur)
            enxuto = pasta / "enxuto.mp4"
            trechos = cortar_pausas(origem, trechos, enxuto)
            novas = remapear(palavras, trechos)
            fala = " ".join(str(w["word"]).strip() for w in novas)
            avisar("Escrevendo gancho e legenda", 0.55)
            t = textos(fala, cfg, cliente)
        d2 = duracao(enxuto)
        avisar("Montando as versões de cada rede", 0.7)
        corte = {"inicio": 0.0, "fim": d2, "titulo": str(t.get("titulo") or "Meu vídeo")[:60], "gancho": str(t.get("gancho") or "").upper()[:48],
                 "motivo": f"Tiramos {round(dur - d2, 1)} s de pausas e muletas ({round(100 * (dur - d2) / dur)}% do vídeo): fica mais rápido e prende mais.",
                 "nota": 85, "legenda": str(t.get("legenda") or ""), "hashtags": [str(h) for h in (t.get("hashtags") or [])][:8]}
        feito = renderizar(str(enxuto), corte, novas, pasta, 1, perfil)
    finally:
        for p in list(pasta.glob("origem.*")) + [pasta / "enxuto.mp4"]:
            p.unlink(missing_ok=True)
    avisar("Pronto", 1.0)
    return {"video": {"titulo": corte["titulo"], "canal": "", "duracao": round(dur, 1), "url": "", "plataforma": "upload"},
            "resumo": f"Vídeo de {round(dur)} s ficou com {round(d2)} s, sem as pausas, com legenda palavra a palavra e uma versão por rede.",
            "cortes": [feito], "dicas": ["Grave em frases curtas: o corte das pausas deixa o ritmo de Reels sozinho.",
                                         "Olhe para a lente nos 3 primeiros segundos: é quando o gancho aparece."]}
