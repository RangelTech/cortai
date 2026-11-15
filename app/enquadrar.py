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
    cap = cv2.VideoCapture(origem)
    if not cap.isOpened():
        return None
    det = _detector()
    cenas = [ini] + [t for t in trocas if ini + 0.5 < t < fim - 0.5] + [fim]
    out: list[tuple[float, float]] = []
    com_rosto = 0
    try:
        for a, b in zip(cenas, cenas[1:]):
            xs = []
            t = a + 0.15
            while t < b:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, quadro = cap.read()
                if not ok:
                    break
                h, w = quadro.shape[:2]
                k = 360 / h
                peq = cv2.resize(quadro, (int(w * k), 360))
                cinza = cv2.equalizeHist(cv2.cvtColor(peq, cv2.COLOR_BGR2GRAY))
                rostos = det.detectMultiScale(cinza, scaleFactor=1.15, minNeighbors=6, minSize=(28, 28))
                if len(rostos):
                    x, y, rw, rh = max(rostos, key=lambda r: r[2] * r[3])
                    xs.append((x + rw / 2) / peq.shape[1])
                t += 0.5
            if xs:
                com_rosto += 1
                cx = statistics.median(xs)
            else:
                cx = out[-1][1] if out else 0.5
            meia = largura_saida_rel / 2
            out.append((round(a - ini, 2), round(min(1 - meia, max(meia, cx)), 4)))
    finally:
        cap.release()
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
