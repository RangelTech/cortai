"""Animação narrada a partir de um texto: roteiro em cenas (IA), narração com voz neural (edge-tts, com o tempo de cada
palavra para a legenda acompanhar), uma imagem por cena (gpt-image pelo llm-access), zoom lento em cada imagem,
destaque no topo e legenda palavra a palavra. Saída 9:16 pronta para Reels/TikTok/Shorts."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx

from app.config import Config
from app.pipeline import ErroUsuario, W, H, _ass_tempo, _ass_texto, _filtro_ass, _json_da_resposta, duracao

log = logging.getLogger("cortai.animacao")
VOZES = {"feminina": "pt-BR-ThalitaMultilingualNeural", "masculina": "pt-BR-AntonioNeural"}

PROMPT = """Você é roteirista de vídeos curtos virais (Reels, TikTok, Shorts) em português do Brasil.
Pedido do cliente: {pedido}
Duração alvo: {segundos} segundos (cerca de {palavras} palavras de narração no total).

Escreva o roteiro em {cenas} cenas. A 1ª cena é o GANCHO: uma frase que prende nos 2 primeiros segundos.
A última cena termina com uma chamada (seguir, comentar ou salvar).
Para cada cena: a fala do narrador (curta, natural, sem emoji), um destaque de 2 a 5 palavras para aparecer grande na tela,
e um prompt de imagem em INGLÊS, detalhado e cinematográfico, vertical, sem texto escrito na imagem e sem pessoas famosas.
Mantenha o mesmo estilo visual em todas as imagens (diga o estilo em todas).

Responda SÓ com JSON:
{{"titulo": "título do vídeo", "estilo": "estilo visual escolhido",
  "cenas": [{{"fala": "...", "destaque": "...", "imagem": "..."}}],
  "legenda": "legenda do post com chamada", "hashtags": ["#..", "#.."]}}"""


def roteiro(pedido: str, segundos: int, cfg: Config, cliente: httpx.Client) -> dict:
    cenas = max(4, min(8, round(segundos / 5)))
    corpo = {"model": cfg.llm_modelo, "temperature": 0.8, "max_tokens": 2500, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "user", "content": PROMPT.format(pedido=pedido, segundos=segundos, palavras=int(segundos * 2.4), cenas=cenas)}]}
    for _ in range(2):
        try:
            r = cliente.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=180)
            r.raise_for_status()
            j = _json_da_resposta(r.json()["choices"][0]["message"]["content"])
            ok = [c for c in j.get("cenas") or [] if str(c.get("fala") or "").strip() and str(c.get("imagem") or "").strip()]
            if len(ok) >= 3:
                j["cenas"] = ok[:8]
                return j
        except Exception as e:  # noqa: BLE001
            log.warning("roteiro falhou: %s", e)
    raise RuntimeError("roteiro: a IA não devolveu cenas válidas")


def narrar(texto: str, voz: str, destino: Path) -> list[dict]:
    """Gera o MP3 e devolve as palavras com início/fim em segundos."""
    import edge_tts

    async def _go():
        com = edge_tts.Communicate(texto, voz, boundary="WordBoundary")
        palavras = []
        with open(destino, "wb") as f:
            async for c in com.stream():
                if c["type"] == "audio":
                    f.write(c["data"])
                elif c["type"] == "WordBoundary":
                    ini = c["offset"] / 1e7
                    palavras.append({"start": ini, "end": ini + c["duration"] / 1e7, "word": c["text"]})
        return palavras
    return asyncio.run(_go())


def imagem(prompt: str, estilo: str, destino: Path, cfg: Config) -> None:
    corpo = {"prompt": f"{prompt}. Style: {estilo}. Vertical 9:16 composition, no text, no watermark.", "size": "1024x1536"}
    ultimo = None
    for _ in range(2):
        try:
            r = httpx.post(cfg.llm_base + "/images/generations", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=240)
            r.raise_for_status()
            destino.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
            return
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise RuntimeError(f"imagem: {ultimo}")


def legenda_cena(palavras: list[dict], destaque: str, dur: float) -> str:
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Leg,Montserrat ExtraBold,56,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,2,2,40,40,300,1
Style: Dest,Montserrat ExtraBold,58,&H00FFFFFF,&H00FFFFFF,&H006E3DFF,&H006E3DFF,1,0,0,0,100,100,0,0,3,16,0,8,70,70,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    linhas = []
    if destaque:
        linhas.append(f"Dialogue: 1,{_ass_tempo(0.1)},{_ass_tempo(dur)},Dest,,0,0,0,,{{\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)}}{_ass_texto(destaque.upper())}")
    grupo = []
    for k, w in enumerate(palavras):
        grupo.append(w)
        prox = palavras[k + 1] if k + 1 < len(palavras) else None
        if len(grupo) >= 3 or not prox or re.search(r"[.!?,;:]$", w["word"]):
            fim = prox["start"] if prox else min(dur, grupo[-1]["end"] + 0.3)
            txt = re.sub(r"[.,;:]+$", "", " ".join(x["word"] for x in grupo)).upper()
            linhas.append(f"Dialogue: 0,{_ass_tempo(grupo[0]['start'])},{_ass_tempo(max(fim, grupo[0]['start'] + 0.2))},Leg,,0,0,0,,{_ass_texto(txt)}")
            grupo = []
    return cab + "\n".join(linhas) + "\n"


def render_cena(img: Path, audio: Path, palavras: list[dict], destaque: str, pasta: Path, n: int) -> Path:
    dur = duracao(audio) + 0.35
    frames = int(dur * 30)
    ass = pasta / f"cena{n}.ass"
    ass.write_text(legenda_cena(palavras, destaque, dur), encoding="utf-8")
    zoom = "min(zoom+0.0009,1.18)" if n % 2 else "if(eq(on,0),1.18,max(zoom-0.0009,1.0))"  # alterna aproxima/afasta
    filtro = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps=30,"
              f"eq=brightness=-0.04,fade=t=in:st=0:d=0.25,fade=t=out:st={dur - 0.25:.2f}:d=0.25,{_filtro_ass(ass)}[v]")
    out = pasta / f"cena{n}.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(img), "-i", str(audio), "-filter_complex", filtro,
                        "-map", "[v]", "-map", "1:a", "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-af", f"apad=pad_dur=0.35", "-threads", "4", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg cena: " + r.stderr[-300:])
    ass.unlink(missing_ok=True)
    return out


def animacao_de_texto(pedido: str, pasta: Path, cfg: Config, avisar: Callable[[str, float], None], segundos: int = 30, voz: str = "feminina") -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    pedido = pedido.strip()
    if len(pedido) < 8:
        raise ErroUsuario("Escreva um pouco mais sobre o vídeo que você quer.")
    segundos = max(15, min(60, int(segundos)))
    with httpx.Client() as cliente:
        avisar("Escrevendo o roteiro", 0.05)
        rt = roteiro(pedido, segundos, cfg, cliente)
    cenas = rt["cenas"]
    estilo = str(rt.get("estilo") or "cinematic digital illustration, dramatic light")
    avisar("Gravando a narração", 0.15)
    falas = []
    for i, c in enumerate(cenas, 1):
        mp3 = pasta / f"cena{i}.mp3"
        falas.append((mp3, narrar(str(c["fala"]), VOZES.get(voz, VOZES["feminina"]), mp3)))
    avisar("Desenhando as cenas", 0.25)
    feitas = [0]

    def _img(i_c):
        i, c = i_c
        imagem(str(c["imagem"]), estilo, pasta / f"cena{i}.png", cfg)
        feitas[0] += 1
        avisar(f"Desenhando as cenas ({feitas[0]} de {len(cenas)})", 0.25 + 0.45 * feitas[0] / len(cenas))
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(_img, enumerate(cenas, 1)))
    partes = []
    for i, c in enumerate(cenas, 1):
        avisar(f"Animando a cena {i} de {len(cenas)}", 0.72 + 0.22 * (i - 1) / len(cenas))
        partes.append(render_cena(pasta / f"cena{i}.png", falas[i - 1][0], falas[i - 1][1], str(c.get("destaque") or ""), pasta, i))
    avisar("Juntando o vídeo", 0.96)
    lista = pasta / "lista.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in partes), encoding="utf-8")
    final = pasta / "corte1.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista.resolve()), "-c", "copy", "-movflags", "+faststart", str(final.resolve())],
                       capture_output=True, text=True, cwd=str(pasta))
    if r.returncode != 0:
        raise RuntimeError("ffmpeg concat: " + r.stderr[-300:])
    capa = pasta / "corte1.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.8", "-i", str(final), "-frames:v", "1", "-q:v", "4", str(capa)], check=False)
    for p in list(pasta.glob("cena*")) + [lista]:
        p.unlink(missing_ok=True)
    dur = duracao(final)
    tags = rt.get("hashtags") or []
    avisar("Pronto", 1.0)
    return {"video": {"titulo": str(rt.get("titulo") or pedido[:60]), "canal": "", "duracao": dur, "url": None, "plataforma": "animacao"},
            "resumo": f"Animação narrada em {len(cenas)} cenas, estilo {estilo}.",
            "roteiro": [{"fala": c["fala"], "destaque": c.get("destaque", "")} for c in cenas],
            "cortes": [{"inicio": 0, "fim": round(dur, 2), "duracao": round(dur, 1), "titulo": str(rt.get("titulo") or "Animação"), "gancho": str(cenas[0].get("destaque") or ""),
                        "motivo": "Roteiro com gancho na 1ª cena, narração natural e legenda palavra a palavra.", "nota": 85,
                        "legenda": str(rt.get("legenda") or ""), "hashtags": [t if str(t).startswith("#") else "#" + str(t) for t in tags][:10],
                        "arquivo": final.name, "capa": capa.name if capa.exists() else None}],
            "dicas": ["Poste nos horários em que seu público mais assiste (geralmente 12h e 19h a 21h).",
                      "Fixe um comentário com uma pergunta para puxar conversa.",
                      "Transforme o mesmo tema numa série: o público volta para ver a próxima."]}


__all__ = ["animacao_de_texto", "VOZES", "json"]
