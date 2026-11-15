"""Tela dividida (react), spec 44 3.6.2: o corte limpo (9:16, sem texto) ganha o vídeo ou a foto do cliente em cima ou embaixo.

Roda ANTES das legendas (estilo.versoes), então legenda, gancho e @ entram por cima da composição final.
Layouts: "cima" (conteúdo em cima, pessoa embaixo, 50/50), "baixo" (invertido), "60_40" (conteúdo 60% em cima).
Vídeo da pessoa mais curto que o corte repete; começa de um ponto diferente em cada corte. Foto vira cartão na cor da marca.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 720, 1280
LAYOUTS = {"cima": (640, "conteudo"), "baixo": (640, "pessoa"), "60_40": (768, "conteudo")}
EXT_VIDEO = (".mp4", ".mov", ".webm", ".m4v", ".mkv")
EXT_FOTO = (".jpg", ".jpeg", ".png", ".webp")
MAX_REACAO_S = 125


def eh_foto(p: Path) -> bool:
    return p.suffix.lower() in EXT_FOTO


def duracao(arq: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(arq)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def tem_audio(arq: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(arq)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _cor(c: str) -> str:
    c = (c or "#111111").lstrip("#")
    return "0x" + (c if len(c) == 6 else "111111")


def aplicar(limpo: Path, reacao: Path, layout: str = "cima", audio_meu: bool = False, cor: str = "#111111", n: int = 1) -> Path:
    """Compõe e devolve o caminho do novo corte limpo (substitui o anterior)."""
    h_cont, primeiro = LAYOUTS.get(layout, LAYOUTS["cima"])
    h_pess = H - h_cont
    dur = duracao(limpo)
    saida = limpo.with_name(limpo.stem + "_tela.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(limpo)]
    foto = eh_foto(reacao)
    if foto:
        cmd += ["-loop", "1", "-i", str(reacao)]
        pessoa = (f"color=c={_cor(cor)}:s={W}x{h_pess}:d={dur:.2f}[fundo];"
                  f"[1:v]scale=-2:{int(h_pess * 0.82)},format=rgba[ft];[fundo][ft]overlay=(W-w)/2:(H-h)/2:shortest=1[pessoa]")
    else:
        rd = max(duracao(reacao), 0.1)
        inicio = (n * 7.3) % rd if rd > dur + 1 else 0.0
        cmd += ["-stream_loop", "-1", "-ss", f"{inicio:.2f}", "-i", str(reacao)]
        pessoa = f"[1:v]scale={W}:{h_pess}:force_original_aspect_ratio=increase,crop={W}:{h_pess},setsar=1[pessoa]"
    conteudo = f"[0:v]crop={W}:{h_cont}:0:(ih-{h_cont})/2,setsar=1[cont]"
    pilha = "[cont][pessoa]vstack=inputs=2[v]" if primeiro == "conteudo" else "[pessoa][cont]vstack=inputs=2[v]"
    filtro = f"{conteudo};{pessoa};{pilha}"
    mapa = ["-map", "[v]"]
    if audio_meu and not foto and tem_audio(reacao):
        filtro += ";[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.7[a]"
        mapa += ["-map", "[a]"]
    else:
        mapa += ["-map", "0:a?"]
    cmd += ["-filter_complex", filtro, *mapa, "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-threads", "4", str(saida)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg tela dividida: " + r.stderr[-400:])
    saida.replace(limpo)
    return limpo
