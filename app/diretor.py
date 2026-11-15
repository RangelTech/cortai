"""Diretor (spec 41, fase 1): cortes que respeitam a cena e validação de cada corte OLHANDO os quadros.

- `cenas(arq)`: instantes de troca de cena (ffmpeg, passada leve a 5 qps em 320 px; limiar 0,18 calibrado: 0,32 nao pegava as trocas de camera de podcast).
- `encaixar(ini, fim, segs, cenas)`: início no começo de frase e, se houver troca de cena até 1,5 s antes, na cena; fim no fim de frase
  e, se houver troca de cena até 1,5 s depois, na cena. Nunca corta frase no meio.
- `validar(...)`: mosaico com 4 quadros do corte renderizado + transcrição do trecho vão ao modelo `visao` (ChatGPT), que devolve
  {ok, nota, problemas, ajuste_inicio, ajuste_fim, gancho_novo}. Quem chama re-renderiza uma vez quando há ajuste.
"""
from __future__ import annotations

import base64
import logging
import re
import subprocess
from pathlib import Path

import httpx

from app.config import Config

log = logging.getLogger("cortai.diretor")
JANELA_CENA = 1.5


def cenas(arq: str, limiar: float = 0.18) -> list[float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", arq, "-an", "-vf", f"fps=5,scale=320:-2,select='gt(scene,{limiar})',showinfo",
                        "-f", "null", "-"], capture_output=True, text=True)
    return sorted({round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)})


def encaixar(ini: float, fim: float, segs: list[dict], trocas: list[float]) -> tuple[float, float]:
    if segs:
        ini = min(segs, key=lambda s: abs(s["start"] - ini))["start"]
        fim = min(segs, key=lambda s: abs(s["end"] - fim))["end"]
    antes = [t for t in trocas if ini - JANELA_CENA <= t <= ini + 0.05]
    if antes:
        ini = max(antes)  # começa na cena mais próxima antes da fala (a fala ainda não começou ou começa junto)
    depois = [t for t in trocas if fim - 0.05 <= t <= fim + JANELA_CENA]
    if depois:
        fim = min(depois)  # termina quando a cena acaba, logo depois da frase
    return round(ini, 2), round(fim, 2)


def mosaico(video: Path, destino: Path) -> Path | None:
    """4 quadros (início, 1/3, 2/3, fim) lado a lado em 2x2, 360 px de largura cada."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        return None
    tempos = [0.4, dur / 3, 2 * dur / 3, max(0.5, dur - 0.6)]
    entradas = sum([["-ss", f"{t:.2f}", "-i", str(video)] for t in tempos], [])
    filtro = "".join(f"[{i}:v]scale=360:-2,trim=end_frame=1[q{i}];" for i in range(4)) + "[q0][q1][q2][q3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[o]"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *entradas, "-filter_complex", filtro, "-map", "[o]", "-frames:v", "1", "-q:v", "5", str(destino)],
                       capture_output=True, text=True)
    return destino if r.returncode == 0 and destino.exists() else None


PROMPT_VALIDA = """Você é editor-chefe de cortes virais (Reels/TikTok/Shorts). A imagem mostra 4 quadros do corte, em ordem (início, 1/3, 2/3, fim).
Título: {titulo}
Gancho no topo: {gancho}
Fala do trecho: "{fala}"

Confira:
1) O trecho faz sentido sozinho e prende no começo?
2) Começa e termina em ponto natural (não no meio de uma fala ou de uma cena)?
3) A pessoa que fala está bem enquadrada (rosto visível, não cortado)?
4) A legenda está legível e não tampa o rosto?
5) O gancho combina com o que é dito?

Responda SÓ com JSON:
{{"ok": true/false, "nota": 0-100, "problemas": ["..."], "ajuste_inicio": segundos para mover o início (negativo = antes, 0 = nada),
  "ajuste_fim": segundos para mover o fim (positivo = depois, 0 = nada), "gancho_novo": "gancho melhor em MAIÚSCULAS ou vazio"}}"""


def validar(video: Path, corte: dict, fala: str, cfg: Config, cliente: httpx.Client) -> dict | None:
    m = mosaico(video, video.with_suffix(".mosaico.jpg"))
    if not m:
        return None
    try:
        b64 = base64.b64encode(m.read_bytes()).decode()
        corpo = {"model": cfg.llm_visao, "max_tokens": 500, "temperature": 0.2, "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT_VALIDA.format(titulo=corte.get("titulo", ""), gancho=corte.get("gancho", ""), fala=fala[:1500])},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]}
        r = cliente.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=120)
        r.raise_for_status()
        from app.pipeline import _json_da_resposta
        j = _json_da_resposta(r.json()["choices"][0]["message"]["content"])
        def num(x):
            try:
                return max(-6.0, min(6.0, float(x or 0)))
            except (TypeError, ValueError):
                return 0.0
        return {"ok": bool(j.get("ok")), "nota": max(0, min(100, int(float(j.get("nota") or 0)))), "problemas": [str(p)[:160] for p in (j.get("problemas") or [])][:5],
                "ajuste_inicio": num(j.get("ajuste_inicio")), "ajuste_fim": num(j.get("ajuste_fim")), "gancho_novo": str(j.get("gancho_novo") or "").upper()[:48]}
    except Exception as e:  # noqa: BLE001  validação é melhoria: sem ela o corte segue
        log.warning("validação falhou: %s", e)
        return None
    finally:
        m.unlink(missing_ok=True)


def fala_do_trecho(segs: list[dict], ini: float, fim: float) -> str:
    return " ".join(s["text"] for s in segs if s["end"] > ini and s["start"] < fim)
