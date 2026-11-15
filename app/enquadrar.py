"""Reenquadramento por rosto: vídeo horizontal vira 9:16 seguindo a pessoa, cena a cena (spec 42, ideia do BuzzAI).

Amostra 2 quadros por segundo do trecho, detecta rostos (Haar do OpenCV, CPU), escolhe por cena o rosto maior
(quem está em primeiro plano) e devolve a posição x do recorte para cada cena. Entre cenas o recorte pula (corte seco,
como um editor faria); dentro da cena fica parado (sem tremer). Sem rosto em mais da metade das cenas: devolve None
e quem chama usa o layout antigo (fundo desfocado).
"""
from __future__ import annotations

import logging
import statistics

log = logging.getLogger("cortai.enquadrar")


def _detector():
    import cv2
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def posicoes(origem: str, ini: float, fim: float, trocas: list[float], largura_saida_rel: float) -> list[tuple[float, float]] | None:
    """[(t_relativo_inicio_da_cena, centro_x_relativo 0..1)] ou None. largura_saida_rel = largura do recorte / largura do vídeo."""
    try:
        import cv2
    except ImportError:
        return None
    # os quadros saem pelo ffmpeg (lê AV1, VP9, H.264...); o OpenCV empacotado não decodifica AV1, que é o que o YouTube entrega
    import subprocess
    import tempfile
    from pathlib import Path
    det = _detector()
    cenas = [ini] + [t for t in trocas if ini + 0.5 < t < fim - 0.5] + [fim]
    out: list[tuple[float, float]] = []
    com_rosto = 0
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{ini:.2f}", "-to", f"{fim:.2f}", "-i", origem, "-an", "-vf", "fps=2,scale=-2:360",
                            "-q:v", "5", str(Path(tmp) / "q%05d.jpg")], capture_output=True, text=True)
        quadros = sorted(Path(tmp).glob("q*.jpg"))
        if r.returncode != 0 or not quadros:
            return None
        achados: list[tuple[float, float | None]] = []
        for i, f in enumerate(quadros):
            img = cv2.imread(str(f))
            if img is None:
                continue
            cinza = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
            rostos = det.detectMultiScale(cinza, scaleFactor=1.15, minNeighbors=6, minSize=(28, 28))
            cx = None
            if len(rostos):
                x, y, rw, rh = max(rostos, key=lambda q: q[2] * q[3])
                cx = (x + rw / 2) / img.shape[1]
            achados.append((ini + i * 0.5 + 0.25, cx))
    for a, b in zip(cenas, cenas[1:]):
        xs = [cx for t, cx in achados if a <= t < b and cx is not None]
        if xs:
            com_rosto += 1
            cx = statistics.median(xs)
        else:
            cx = out[-1][1] if out else 0.5
        meia = largura_saida_rel / 2
        out.append((round(a - ini, 2), round(min(1 - meia, max(meia, cx)), 4)))
    n = len(cenas) - 1
    if n <= 0 or com_rosto < max(1, n / 2):
        return None
    # cenas seguidas com o mesmo enquadramento (diferença < 4%) viram uma só: menos pulos à toa
    junto = [out[0]]
    for p in out[1:]:
        if abs(p[1] - junto[-1][1]) >= 0.04:
            junto.append(p)
    return junto


def expressao_x(pos: list[tuple[float, float]], largura_video: int, largura_recorte: int) -> str:
    """Expressão do filtro crop (x por tempo): if(lt(t,t1),x0,if(lt(t,t2),x1,...))."""
    xs = [max(0, min(largura_video - largura_recorte, int(c * largura_video - largura_recorte / 2))) for _, c in pos]
    expr = str(xs[-1])
    for (t, _), x in reversed(list(zip(pos[1:], xs[:-1]))):
        expr = f"if(lt(t,{t:.2f}),{x},{expr})"
    return expr
