"""Repertório de ganchos e estilos visuais usados nos prompts (spec 42: ideias do grupo Salvar)."""

GANCHOS = [
    "Ninguém está falando sobre isso", "Eu queria ter descoberto isso antes", "Pega o hábito de…", "Faça isso para…",
    "Aqui está a verdade", "É assim que…", "Isso pode te surpreender", "Você não precisa disso agora",
    "Talvez você não concorde com isso", "Acabei de descobrir isso", "O erro que quase todo mundo comete",
    "3 coisas que ninguém te conta sobre…",
]

REPERTORIO = "Aberturas que costumam prender (use como inspiração, adapte ao assunto, nunca copie sem sentido): " + "; ".join(f'"{g}"' for g in GANCHOS) + "."

# código -> (nome no painel, estilo em inglês para o gerador de imagem)
ESTILOS = {
    "cinema": ("Cinematográfico", "cinematic digital illustration, dramatic volumetric light, rich colors, film still"),
    "3d": ("3D animado", "3D animated movie render, soft global illumination, stylized characters, Pixar-like quality but original"),
    "lego": ("Brinquedo de montar", "toy brick world, plastic brick minifigure style, macro photography of toys, bright studio light"),
    "pixel": ("Pixel art", "detailed 16-bit pixel art, retro video game scene, crisp pixels, vibrant palette"),
    "quadro": ("Quadro negro", "white chalk drawing on a dark green chalkboard, hand-drawn diagrams and doodles, chalk texture"),
    "ps2": ("Videogame anos 2000", "early 2000s PlayStation 2 era video game graphics, low-poly 3D, nostalgic, slight CRT look"),
    "hq": ("História em quadrinhos", "comic book panel, bold ink outlines, halftone shading, dynamic composition, vivid flat colors"),
    "vetor": ("Ilustração vetorial", "clean flat vector illustration, minimal shapes, modern editorial style, soft gradients"),
    "neon": ("Neon que brilha", "glowing neon line art on black background, luminous strokes, synthwave colors, light trails"),
    "dark": ("Canal dark", "dark moody cinematic scene, deep shadows, mysterious atmosphere, cold teal and orange light"),
}


def estilo_prompt(codigo: str | None) -> str | None:
    e = ESTILOS.get((codigo or "").strip())
    return e[1] if e else None
