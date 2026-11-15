"""Clonar um Reels/TikTok/Shorts: estuda o vídeo de referência (duração, ritmo de cortes, gancho, estrutura da fala) e
recria o MESMO formato com o tema do cliente, pelo motor de animação narrada (roteiro → voz → imagens → render)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

import httpx

from app import animacao
from app.config import Config
from app.pipeline import ErroUsuario, _json_da_resposta, baixar, duracao, extrair_audio, transcrever


def cortes_de_cena(arq: str) -> list[float]:
    """Instantes (s) em que a imagem muda de cena (filtro scdet do ffmpeg)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", arq, "-vf", "scdet=threshold=12", "-an", "-f", "null", "-"], capture_output=True, text=True)
    return [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", r.stderr)]


ANALISE = """Você analisa vídeos curtos virais. Abaixo está a transcrição de um Reels de referência com o tempo de cada frase,
a duração total e quantas trocas de cena ele tem. Descreva a FÓRMULA dele para que outro vídeo possa copiar o formato
(não o conteúdo). Responda SÓ com JSON:
{{"gancho": "como ele prende nos 2 primeiros segundos (tipo de frase)", "estrutura": ["etapa 1 ...", "etapa 2 ..."],
  "tom": "tom de voz e ritmo", "chamada": "como termina", "por_que_funciona": "1 a 2 frases"}}

Duração: {dur:.0f} s. Trocas de cena: {cenas} (uma a cada {ritmo:.1f} s).
Transcrição:
{texto}"""


def clonar_reels(url: str, tema: str, pasta: Path, cfg: Config, avisar: Callable[[str, float], None], voz: str = "feminina") -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    tema = (tema or "").strip()
    if len(tema) < 6:
        raise ErroUsuario("Conte o tema do seu vídeo (sobre o que ele vai falar).")
    with httpx.Client() as cliente:
        avisar("Baixando o vídeo de referência", 0.03)
        info = baixar(url, pasta, cfg)
        try:
            if info["duracao"] > 180:
                raise ErroUsuario("Para clonar, mande um vídeo curto (até 3 minutos), como um Reels, TikTok ou Shorts.")
            avisar("Estudando o ritmo e a fala", 0.08)
            cenas = cortes_de_cena(info["arquivo"])
            audio = extrair_audio(info["arquivo"], pasta)
            try:
                trans = transcrever(audio, cfg, cliente)
            except Exception:  # noqa: BLE001  vídeo sem fala: segue só com o ritmo
                trans = {"segments": [], "duration": info["duracao"]}
            audio.unlink(missing_ok=True)
        finally:
            for p in pasta.glob("origem.*"):
                p.unlink(missing_ok=True)
        dur = float(info["duracao"] or trans.get("duration") or 30)
        ritmo = dur / max(1, len(cenas) + 1)
        texto = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in trans.get("segments", []))[:12000] or "(sem fala: vídeo só com música e texto)"
        corpo = {"model": cfg.llm_modelo, "temperature": 0.4, "max_tokens": 1200, "chat_template_kwargs": {"enable_thinking": False},
                 "messages": [{"role": "user", "content": ANALISE.format(dur=dur, cenas=len(cenas), ritmo=ritmo, texto=texto)}]}
        formula = {}
        for modelo in dict.fromkeys([cfg.llm_planejador or cfg.llm_modelo, cfg.llm_modelo]):  # diretor GPT analisa a fórmula; Qwen se falhar
            corpo["model"] = modelo
            try:
                r = cliente.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, json=corpo, timeout=180)
                r.raise_for_status()
                formula = _json_da_resposta(r.json()["choices"][0]["message"]["content"])
                break
            except Exception:  # noqa: BLE001
                formula = {}
    segundos = int(max(15, min(60, round(dur))))
    pedido = (f"{tema}\n\nCOPIE A FÓRMULA deste vídeo de referência (formato, não o conteúdo): gancho = {formula.get('gancho', 'frase forte de curiosidade')}; "
              f"estrutura = {' → '.join(formula.get('estrutura', [])) or 'gancho, desenvolvimento rápido, virada, chamada'}; tom = {formula.get('tom', 'animado e direto')}; "
              f"final = {formula.get('chamada', 'pedir para seguir')}. Uma cena nova a cada {ritmo:.1f} segundos, mais ou menos.")
    res = animacao.animacao_de_texto(pedido, pasta, cfg, lambda e, p: avisar(e, 0.15 + 0.85 * p), segundos, voz)
    res["video"]["url"] = url
    res["video"]["plataforma"] = "clone"
    res["referencia"] = {"titulo": info["titulo"], "canal": info["canal"], "duracao": round(dur, 1), "trocas_de_cena": len(cenas), "formula": formula}
    res["resumo"] = f"Mesmo formato de \"{info['titulo'][:60]}\": {len(cenas)} trocas de cena em {dur:.0f} s, recriado com o seu tema."
    if formula.get("por_que_funciona"):
        res["cortes"][0]["motivo"] = "Fórmula copiada: " + str(formula["por_que_funciona"])[:300]
    return res


__all__ = ["clonar_reels", "cortes_de_cena", "json", "duracao"]
