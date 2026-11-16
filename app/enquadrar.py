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


YUNET = __import__("pathlib").Path(__file__).parent / "modelos" / "face_detection_yunet_2023mar.onnx"  # OpenCV Zoo, MIT


class _Detector:
    """YuNet (rede neural do OpenCV, pega rosto de lado, com chapéu e óculos) e, se não houver, Haar de frente + perfil.
    16/11/2026: só o Haar de frente perdia o rosto de quem estava de chapéu/perfil e o recorte saía na borda."""

    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.yunet = None
        if YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 50)
            except Exception as e:  # noqa: BLE001
                log.warning("YuNet indisponivel (%s): uso Haar", e)
        base = cv2.data.haarcascades
        self.frente = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        self.perfil = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")

    def maior(self, img) -> tuple[float, float, float, float] | None:
        """Maior rosto do quadro (x, y, largura, altura em pixels) ou None."""
        cv2 = self.cv2
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(img)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                return float(f[0]), float(f[1]), float(f[2]), float(f[3])
        cinza = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        achados = list(self.frente.detectMultiScale(cinza, scaleFactor=1.15, minNeighbors=6, minSize=(28, 28)))
        achados += list(self.perfil.detectMultiScale(cinza, scaleFactor=1.15, minNeighbors=5, minSize=(28, 28)))
        espelho = cv2.flip(cinza, 1)  # o Haar de perfil só vê um lado
        achados += [(w - x - rw, y, rw, rh) for x, y, rw, rh in self.perfil.detectMultiScale(espelho, scaleFactor=1.15, minNeighbors=5, minSize=(28, 28))]
        if not achados:
            return None
        x, y, rw, rh = max(achados, key=lambda q: q[2] * q[3])
        return float(x), float(y), float(rw), float(rh)


def _detector() -> _Detector:
    return _Detector()


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
            rosto = det.maior(img)
            cx = (rosto[0] + rosto[2] / 2) / img.shape[1] if rosto else None
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
