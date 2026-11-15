"""Cortes virais a partir de um link: baixa, transcreve com tempos, a IA escolhe os trechos, renderiza 9:16 com legenda
queimada e gancho no topo, e escreve o relatório de cada corte.

Cada etapa chama `avisar(etapa, progresso)` para a tela mostrar o andamento. O vídeo de origem é apagado no fim
(o disco da VPS é pequeno); ficam só os cortes, as capas e o relatório.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import httpx

from app.config import Config
from app.ganchos import REPERTORIO

log = logging.getLogger("cortai.pipeline")
FONTES = Path(__file__).resolve().parent.parent / "static" / "fonts"
W, H = 720, 1280


class ErroUsuario(Exception):
    """Erro que pode ser mostrado ao cliente como está (link inválido, vídeo longo demais...)."""


# ---------------------------------------------------------------- download
def baixar(url: str, pasta: Path, cfg: Config) -> dict:
    import yt_dlp

    opcoes = {
        "outtmpl": str(pasta / "origem.%(ext)s"),
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "max_filesize": 4_000_000_000,
    }
    if cfg.proxy:
        opcoes["proxy"] = cfg.proxy
    try:
        with yt_dlp.YoutubeDL({**opcoes, "skip_download": True}) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        log.warning("yt-dlp info falhou: %s", msg[:300])
        if "Private" in msg or "login" in msg.lower() or "sign in" in msg.lower():
            raise ErroUsuario("Esse vídeo pede login ou é privado. Use um link público.") from e
        raise ErroUsuario("Não consegui abrir esse link. Confira se o vídeo é público e se o endereço está completo.") from e
    if info.get("_type") == "playlist":
        raise ErroUsuario("Esse link é de uma playlist. Mande o link de um vídeo só.")
    dur = float(info.get("duration") or 0)
    if dur and dur > cfg.max_duracao_s:
        raise ErroUsuario(f"O vídeo tem {int(dur // 60)} minutos. O limite é {cfg.max_duracao_s // 60} minutos.")
    if info.get("is_live"):
        raise ErroUsuario("Transmissão ao vivo ainda não funciona. Mande o link depois que a live terminar.")
    if dur > 90 * 60:  # live longa: 480p basta para o corte vertical e o download cabe no disco
        opcoes["format"] = "bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]/b"
    with yt_dlp.YoutubeDL(opcoes) as y:
        y.download([url])
    arq = next((p for p in pasta.glob("origem.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov")), None)
    if not arq:
        raise ErroUsuario("Não consegui baixar esse vídeo.")
    return {"arquivo": str(arq), "titulo": info.get("title") or "vídeo", "canal": info.get("uploader") or info.get("channel") or "",
            "duracao": dur or duracao(arq), "url": url, "plataforma": info.get("extractor_key") or ""}


def duracao(arq) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arq)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def tamanho_video(arq) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(arq)],
                       capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except ValueError:
        return 0, 0


# ---------------------------------------------------------------- transcrição
def extrair_audio(arq: str, pasta: Path) -> Path:
    out = pasta / "audio.ogg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", arq, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", str(out)], check=True)
    return out


PEDACO_S = 30 * 60


def transcrever(audio: Path, cfg: Config, cliente: httpx.Client) -> dict:
    """Áudio longo (live de horas) vai em pedaços de 30 min; os tempos de cada pedaço são somados ao deslocamento."""
    dur = duracao(audio)
    if dur <= PEDACO_S + 120:
        return _transcrever_um(audio, cfg, cliente)
    pasta = audio.parent / "pedacos"
    pasta.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-f", "segment", "-segment_time", str(PEDACO_S), "-reset_timestamps", "1", "-c", "copy",
                    str(pasta / "p%03d.ogg")], check=True)
    segs, words, desloc = [], [], 0.0
    try:
        for p in sorted(pasta.glob("p*.ogg")):
            dp = duracao(p)
            if dp < 1:
                continue
            j = _transcrever_um(p, cfg, cliente)
            segs += [{**s, "start": s["start"] + desloc, "end": s["end"] + desloc} for s in j["segments"]]
            words += [{**w, "start": w["start"] + desloc, "end": w["end"] + desloc} for w in j.get("words") or []]
            desloc += dp
    finally:
        for p in pasta.glob("*"):
            p.unlink(missing_ok=True)
        pasta.rmdir()
    return {"segments": segs, "words": words, "duration": dur, "text": " ".join(s["text"] for s in segs)}


def picos_de_volume(audio: Path, max_picos: int = 40) -> list[float]:
    """Segundos com grito, risada ou reação (volume bem acima do normal do vídeo). Bom para live e gameplay."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af",
                        "aresample=8000,asetnsamples=8000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                        "-f", "null", "-"], capture_output=True, text=True)
    niveis = [float(x) for x in re.findall(r"RMS_level=(-?[0-9.]+)", r.stderr) if x not in ("-inf", "inf")]
    if len(niveis) < 60:
        return []
    ordenados = sorted(niveis)
    corte = ordenados[len(ordenados) // 2] + 12  # 12 dB acima da mediana
    picos: list[float] = []
    for t, v in sorted(enumerate(niveis), key=lambda x: -x[1]):
        if v < corte or len(picos) >= max_picos:
            break
        if all(abs(t - p) > 20 for p in picos):
            picos.append(float(t))
    return sorted(picos)


def _transcrever_um(audio: Path, cfg: Config, cliente: httpx.Client, tentativas: int = 4) -> dict:
    """Erro passageiro (5xx, conexão caída, reinício do gateway) tenta de novo com espera crescente; 4xx não."""
    import time
    r = None
    for k in range(tentativas):
        try:
            with open(audio, "rb") as f:
                r = cliente.post(cfg.llm_base + "/audio/transcriptions", headers={"Authorization": f"Bearer {cfg.llm_key}"},
                                 files={"file": ("audio.ogg", f, "audio/ogg")},
                                 data={"language": "pt", "response_format": "verbose_json"}, timeout=1800)
            if r.status_code < 500:
                break
        except httpx.HTTPError as e:
            log.warning("transcricao: tentativa %s falhou: %s", k + 1, e)
            r = None
        if k < tentativas - 1:
            time.sleep(min(120, 15 * 2 ** k))
    if r is None:
        raise RuntimeError("transcricao: sem resposta do servidor")
    if r.status_code != 200:
        raise RuntimeError(f"transcricao {r.status_code}: {r.text[:200]}")
    j = r.json()
    if not j.get("segments"):
        raise RuntimeError("transcricao sem tempos (segments)")
    return j


# ---------------------------------------------------------------- escolha dos trechos
PROMPT = """Você é editor de vídeos virais para Reels, TikTok e Shorts. Abaixo está a transcrição de um vídeo com o tempo de cada frase em segundos.
Escolha os {n} MELHORES trechos para virar cortes virais independentes.

Regras:
- Cada corte tem entre 20 e 60 segundos, começa no início de uma frase e termina no fim de uma frase.
- O corte precisa fazer sentido sozinho, sem o resto do vídeo, e prender nos 3 primeiros segundos.
- Prefira: opinião forte, história com reviravolta, dica prática, número surpreendente, frase polêmica, humor, emoção.
- Os cortes não podem se sobrepor.
- Escreva em português do Brasil.
- {repertorio}

Responda SÓ com JSON, neste formato:
{{"resumo": "do que o vídeo trata, em 1 frase",
  "cortes": [{{"inicio": 12.3, "fim": 48.0, "titulo": "título curto e chamativo (até 60 letras)",
    "gancho": "texto curto para o topo do vídeo nos 3 primeiros segundos (até 40 letras, MAIÚSCULAS)",
    "motivo": "por que esse trecho tem potencial de viralizar (1 a 2 frases)",
    "nota": 0-100, "legenda": "legenda para o post, com chamada para comentar", "hashtags": ["#exemplo", "..."]}}]}}

Transcrição:
{texto}"""


def _texto_transcricao(segs: list[dict], limite: int = 60000) -> str:
    linhas = [f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs]
    t = "\n".join(linhas)
    if len(t) <= limite:
        return t
    passo = math.ceil(len(t) / limite)  # vídeo muito longo: amostra uniforme das frases
    return "\n".join(linhas[::passo])


def _json_da_resposta(txt: str) -> dict:
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("sem JSON")
    return json.loads(m.group(0))


JANELA_TXT = 45000


def escolher(trans: dict, cfg: Config, cliente: httpx.Client, n: int, trocas: list[float] | None = None, picos: list[float] | None = None) -> dict:
    """Vídeo curto: uma escolha só. Live longa: escolhe candidatos em janelas da transcrição e fica com os de nota maior."""
    segs = trans["segments"]
    dur = float(trans.get("duration") or (segs[-1]["end"] if segs else 0))
    n = max(1, min(n, cfg.max_cortes, int(dur // 25) or 1))
    linhas = [f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs]
    if sum(len(x) + 1 for x in linhas) <= JANELA_TXT * 1.3:
        return _escolher_janela(segs, segs, dur, cfg, cliente, n, trocas, picos)
    janelas, atual, tam = [], [], 0
    for s, l in zip(segs, linhas):
        if tam + len(l) > JANELA_TXT and atual:
            janelas.append(atual); atual, tam = [], 0
        atual.append(s); tam += len(l) + 1
    if atual:
        janelas.append(atual)
    candidatos, resumos = [], []
    for jan in janelas:
        p_jan = [p for p in (picos or []) if jan[0]["start"] <= p <= jan[-1]["end"]]
        r = _escolher_janela(jan, segs, dur, cfg, cliente, min(n, 3), trocas, p_jan)
        candidatos += r["cortes"]; resumos.append(r["resumo"])
    candidatos.sort(key=lambda c: -float(c.get("nota") or 0))
    escolhidos: list[dict] = []
    for c in candidatos:
        if all(c["fim"] <= e["inicio"] or c["inicio"] >= e["fim"] for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) >= n:
            break
    escolhidos.sort(key=lambda c: c["inicio"])
    for k, c in enumerate(escolhidos, 1):
        if re.fullmatch(r"Corte \d+", str(c.get("titulo") or "")):
            c["titulo"] = f"Corte {k}"
    return {"resumo": next((x for x in resumos if x), ""), "cortes": escolhidos}


def _escolher_janela(segs_jan: list[dict], segs: list[dict], dur: float, cfg: Config, cliente: httpx.Client, n: int,
                     trocas: list[float] | None, picos: list[float] | None) -> dict:
    texto = _texto_transcricao(segs_jan)
    if picos:
        texto += "\n\nMomentos de grito, risada ou reação forte (pico de volume, em segundos): " + ", ".join(f"{p:.0f}" for p in picos) + \
                 ". Em live e gameplay, esses momentos costumam ser os melhores cortes: prefira trechos que contenham um deles."
    corpo = {"model": cfg.llm_modelo, "temperature": 0.4, "max_tokens": 3000, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "user", "content": PROMPT.format(n=n, texto=texto, repertorio=REPERTORIO)}]}
    ultimo = None
    for modelo in dict.fromkeys([cfg.llm_planejador, cfg.llm_modelo]):  # planejador (melhor julgamento) e, se falhar, o modelo da casa
        corpo["model"] = modelo
        try:
            r = cliente.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=300)
            r.raise_for_status()
            bruto = _json_da_resposta(r.json()["choices"][0]["message"]["content"])
            cortes = validar_cortes(bruto.get("cortes") or [], segs, dur, trocas)
            if cortes:
                return {"resumo": str(bruto.get("resumo") or "")[:300], "cortes": cortes[:n]}
        except Exception as e:  # noqa: BLE001
            ultimo = e
            log.warning("escolha pela IA falhou: %s", e)
    log.warning("usando escolha automatica (sem IA): %s", ultimo)
    fim_jan = segs_jan[-1]["end"] if segs_jan else dur
    return {"resumo": "", "cortes": escolha_automatica(segs_jan, fim_jan, n)}


def _ajustar_a_frases(ini: float, fim: float, segs: list[dict]) -> tuple[float, float]:
    """Encosta o início no começo de uma frase e o fim no fim de uma frase (sem cortar palavra)."""
    if not segs:
        return ini, fim
    comeco = min(segs, key=lambda s: abs(s["start"] - ini))["start"]
    final = min(segs, key=lambda s: abs(s["end"] - fim))["end"]
    return comeco, final


def validar_cortes(cortes: list, segs: list[dict], dur: float, trocas: list[float] | None = None) -> list[dict]:
    ok = []
    for c in cortes:
        try:
            ini, fim = float(c["inicio"]), float(c["fim"])
        except (KeyError, TypeError, ValueError):
            continue
        if trocas:
            from app.diretor import encaixar
            ini, fim = encaixar(max(0.0, ini), min(dur or fim, fim), segs, trocas)
        else:
            ini, fim = _ajustar_a_frases(max(0.0, ini), min(dur or fim, fim), segs)
        if fim - ini < 12:
            continue
        if fim - ini > 90:
            fim = ini + 60
            fim = _ajustar_a_frases(ini, fim, segs)[1]
            if fim - ini < 12 or fim - ini > 90:
                fim = ini + 60
        if any(not (fim <= o["inicio"] or ini >= o["fim"]) for o in ok):
            continue
        tags = c.get("hashtags") or []
        if isinstance(tags, str):
            tags = re.findall(r"#\w+", tags)
        ok.append({
            "inicio": round(ini, 2), "fim": round(fim, 2),
            "titulo": str(c.get("titulo") or "Corte")[:80],
            "gancho": str(c.get("gancho") or "").upper()[:48],
            "motivo": str(c.get("motivo") or "")[:400],
            "nota": max(0, min(100, int(float(c.get("nota") or 70)))),
            "legenda": str(c.get("legenda") or "")[:600],
            "hashtags": [str(t) if str(t).startswith("#") else "#" + str(t) for t in tags][:10],
        })
    return sorted(ok, key=lambda c: -c["nota"])


def escolha_automatica(segs: list[dict], dur: float, n: int) -> list[dict]:
    """Reserva quando a IA falha: janelas de ~40 s com mais fala por segundo, sem sobrepor."""
    janelas = []
    for i, s in enumerate(segs):
        ini, fim, palavras, j = s["start"], s["start"], 0, i
        while j < len(segs) and segs[j]["end"] - ini <= 45:
            fim = segs[j]["end"]
            palavras += len(segs[j]["text"].split())
            j += 1
        if fim - ini >= 20:
            janelas.append((palavras / (fim - ini), ini, fim))
    escolhidos = []
    for _, ini, fim in sorted(janelas, reverse=True):
        if all(fim <= c["inicio"] or ini >= c["fim"] for c in escolhidos):
            escolhidos.append({"inicio": round(ini, 2), "fim": round(fim, 2), "titulo": f"Corte {len(escolhidos) + 1}", "gancho": "",
                               "motivo": "Trecho com fala intensa e contínua.", "nota": 60, "legenda": "", "hashtags": []})
        if len(escolhidos) >= n:
            break
    return escolhidos


# ---------------------------------------------------------------- legenda e render
def _ass_tempo(t: float) -> str:
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_texto(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def legenda_ass(palavras: list[dict], ini: float, fim: float, gancho: str) -> str:
    """Legenda estilo Reels: 2 a 4 palavras por vez, maiúsculas, fonte grossa com contorno; gancho no topo nos 3 primeiros s."""
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Leg,Montserrat ExtraBold,58,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,2,2,40,40,330,1
Style: Gancho,Montserrat ExtraBold,52,&H00FFFFFF,&H00FFFFFF,&H006E3DFF,&H006E3DFF,1,0,0,0,100,100,0,0,3,14,0,8,50,50,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    linhas = []
    if gancho:
        linhas.append(f"Dialogue: 1,{_ass_tempo(0)},{_ass_tempo(min(3.2, fim - ini))},Gancho,,0,0,0,,{_ass_texto(gancho)}")
    dentro = [w for w in palavras if w["end"] > ini and w["start"] < fim and w["word"].strip()]
    grupo: list[dict] = []
    for k, w in enumerate(dentro):
        grupo.append(w)
        prox = dentro[k + 1] if k + 1 < len(dentro) else None
        quebra = (len(grupo) >= 3 or re.search(r"[.!?,;:]$", w["word"]) or not prox or prox["start"] - w["end"] > 0.6
                  or sum(len(x["word"]) for x in grupo) > 16)
        if quebra:
            a = grupo[0]["start"] - ini
            b = (prox["start"] - ini) if prox and prox["start"] - grupo[-1]["end"] < 0.6 else grupo[-1]["end"] - ini
            texto = " ".join(x["word"].strip() for x in grupo).upper()
            texto = re.sub(r"[.,;:]+$", "", texto)
            linhas.append(f"Dialogue: 0,{_ass_tempo(a)},{_ass_tempo(max(b, a + 0.25))},Leg,,0,0,0,,{_ass_texto(texto)}")
            grupo = []
    return cab + "\n".join(linhas) + "\n"


def _filtro_ass(caminho: Path) -> str:
    """Caminho para o filtro subtitles do ffmpeg (escapar ':' e '\\' e aspas)."""
    p = str(caminho).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    f = str(FONTES).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{p}':fontsdir='{f}'"


def renderizar(origem: str, corte: dict, palavras: list[dict], pasta: Path, n: int, perfil: dict | None = None) -> dict:
    """Corte limpo (vídeo 9:16 + áudio, sem texto, guardado para editar) e uma versão por rede com legenda, gancho e @ (estilo.py)."""
    from app import estilo
    ini, fim = corte["inicio"], corte["fim"]
    w, h = tamanho_video(origem)
    if h and w and h >= w:  # já é vertical: só ajusta ao quadro 9:16
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0]"
    else:  # horizontal: fundo desfocado + vídeo no meio, um pouco ampliado
        base = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.12[fundo];"
                f"[b]crop='min(iw,ih*3/4)':ih,scale={W}:{int(W * 4 / 3)}[frente];[fundo][frente]overlay=(W-w)/2:(H-h)/2-70,setsar=1[v0]")
    filtro = base.replace("[v0]", "[v]")
    out = pasta / f"corte{n}_limpo.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ini:.2f}", "-to", f"{fim:.2f}", "-i", origem,
           "-filter_complex", filtro, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-movflags", "+faststart",
           "-threads", "4", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr[-400:])
    rel = [{"start": round(w_["start"] - ini, 2), "end": round(w_["end"] - ini, 2), "word": w_["word"]} for w_ in palavras
           if w_["end"] > ini and w_["start"] < fim and str(w_["word"]).strip()]
    arquivos = estilo.versoes(out, rel, corte.get("gancho", ""), pasta, f"corte{n}", perfil or {})
    principal = pasta / next(iter(arquivos.values()))
    capa = pasta / f"corte{n}.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(principal), "-frames:v", "1", "-q:v", "4", str(capa)], check=False)
    return {**corte, "arquivo": principal.name, "arquivos": arquivos, "limpo": out.name, "palavras": rel, "legenda_on": True,
            "capa": capa.name if capa.exists() else None, "duracao": round(fim - ini, 1)}


# ---------------------------------------------------------------- tudo junto
def cortes_de_link(url: str, pasta: Path, cfg: Config, avisar: Callable[[str, float], None], quantos: int = 3, perfil: dict | None = None) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    with httpx.Client() as cliente:
        avisar("Baixando o vídeo", 0.05)
        info = baixar(url, pasta, cfg)
        try:
            avisar("Ouvindo e transcrevendo a fala", 0.25)
            audio = extrair_audio(info["arquivo"], pasta)
            trans = transcrever(audio, cfg, cliente)
            picos = picos_de_volume(audio)
            audio.unlink(missing_ok=True)
            if len(" ".join(s["text"] for s in trans["segments"]).split()) < 25:
                raise ErroUsuario("Quase não tem fala nesse vídeo. Os cortes virais precisam de alguém falando.")
            from app import diretor
            avisar("Encontrando as trocas de cena", 0.38)
            trocas = diretor.cenas(info["arquivo"], fps=5 if float(info.get("duracao") or 0) <= 3600 else 2)
            avisar("Procurando os momentos com mais potencial", 0.45)
            escolha = escolher(trans, cfg, cliente, quantos, trocas, picos)
            prontos = []
            palavras = trans.get("words") or []
            segs = trans["segments"]
            total = len(escolha["cortes"])
            for k, c in enumerate(escolha["cortes"], 1):
                base = 0.5 + 0.45 * (k - 1) / max(1, total)
                avisar(f"Editando o corte {k} de {total}", base)
                feito = renderizar(info["arquivo"], c, palavras, pasta, k, perfil)
                if cfg.validar:
                    avisar(f"Conferindo o corte {k} de {total}", base + 0.15 / max(1, total))
                    v = diretor.validar(pasta / feito["arquivo"], c, diretor.fala_do_trecho(segs, c["inicio"], c["fim"]), cfg, cliente)
                    # refaz quando reprovou, ou passou raspando (nota < 85) com ajuste sugerido; no máximo 1 vez
                    if v and (not v["ok"] or v["nota"] < 85) and (v["ajuste_inicio"] or v["ajuste_fim"] or v["gancho_novo"]):
                        novo = dict(c)
                        ini2, fim2 = diretor.encaixar(c["inicio"] + v["ajuste_inicio"], c["fim"] + v["ajuste_fim"], segs, trocas)
                        if 12 <= fim2 - ini2 <= 90:
                            novo["inicio"], novo["fim"] = ini2, fim2
                        if v["gancho_novo"]:
                            novo["gancho"] = v["gancho_novo"]
                        avisar(f"Refazendo o corte {k} de {total}", base + 0.25 / max(1, total))
                        feito = renderizar(info["arquivo"], novo, palavras, pasta, k, perfil)
                        v2 = diretor.validar(pasta / feito["arquivo"], novo, diretor.fala_do_trecho(segs, novo["inicio"], novo["fim"]), cfg, cliente)
                        v = v2 or v
                        v["refeito"] = True
                    if v:
                        feito["validacao"] = v
                prontos.append(feito)
            compilado = None
            if float(info.get("duracao") or 0) >= 20 * 60 and len(prontos) >= 2:  # live/podcast longo: melhores momentos horizontal p/ YouTube
                avisar("Montando o vídeo de melhores momentos", 0.97)
                try:
                    compilado = compilado_horizontal(info["arquivo"], [(c["inicio"], c["fim"]) for c in prontos], pasta / "melhores_momentos.mp4")
                except Exception as e:  # noqa: BLE001
                    log.warning("compilado falhou: %s", e)
        finally:
            for p in pasta.glob("origem.*"):
                p.unlink(missing_ok=True)
    avisar("Pronto", 1.0)
    return {"video": {k: info[k] for k in ("titulo", "canal", "duracao", "url", "plataforma")}, "resumo": escolha["resumo"],
            "cortes": prontos, "dicas": DICAS, **({"compilado": compilado} if compilado else {})}


def compilado_horizontal(origem: str, trechos: list[tuple[float, float]], destino: Path) -> str:
    """Junta os trechos escolhidos (na ordem do vídeo) no formato original 16:9, com fade curto entre eles."""
    trechos = sorted(trechos)
    partes, rot = [], []
    for i, (a, b) in enumerate(trechos):
        d = b - a
        partes.append(f"[0:v]trim={a:.2f}:{b:.2f},setpts=PTS-STARTPTS,scale=-2:720,fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, d - 0.4):.2f}:d=0.4[v{i}];"
                      f"[0:a]atrim={a:.2f}:{b:.2f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.3,afade=t=out:st={max(0, d - 0.3):.2f}:d=0.3[a{i}];")
        rot.append(f"[v{i}][a{i}]")
    filtro = "".join(partes) + "".join(rot) + f"concat=n={len(trechos)}:v=1:a=1[v][a]"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", origem, "-filter_complex", filtro, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-threads", "4", str(destino)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (compilado): " + r.stderr[-300:])
    return destino.name


DICAS = [
    "Poste o corte de nota mais alta primeiro e os outros nos dias seguintes.",
    "Os 3 primeiros segundos decidem tudo: o gancho já vem escrito no topo do vídeo.",
    "Responda os primeiros comentários na primeira hora: isso aumenta o alcance.",
    "Use a legenda e as hashtags sugeridas, trocando por palavras do seu nicho se quiser.",
    "Dê crédito ao criador do vídeo original na legenda.",
]


def limpar_pasta(pasta: Path) -> None:
    shutil.rmtree(pasta, ignore_errors=True)


def tamanho_pasta(pasta: Path) -> int:
    return sum(f.stat().st_size for f in pasta.rglob("*") if f.is_file()) if pasta.exists() else 0


def disco_livre(caminho: str) -> int:
    try:
        return shutil.disk_usage(caminho).free
    except OSError:
        return 0


__all__ = ["cortes_de_link", "ErroUsuario", "legenda_ass", "validar_cortes", "escolha_automatica", "limpar_pasta", "disco_livre",
           "tamanho_pasta", "os"]
