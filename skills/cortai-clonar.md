---
name: cortai-clonar
description: prompt-modelo de cortai/app/clonar.py (ANALISE). Mantenha os campos entre chaves.
---
Você analisa vídeos curtos virais. Abaixo está a transcrição de um Reels de referência com o tempo de cada frase,
a duração total e quantas trocas de cena ele tem. Descreva a FÓRMULA dele para que outro vídeo possa copiar o formato
(não o conteúdo). Responda SÓ com JSON:
{{"gancho": "como ele prende nos 2 primeiros segundos (tipo de frase)", "estrutura": ["etapa 1 ...", "etapa 2 ..."],
  "tom": "tom de voz e ritmo", "chamada": "como termina", "por_que_funciona": "1 a 2 frases"}}

Duração: {dur:.0f} s. Trocas de cena: {cenas} (uma a cada {ritmo:.1f} s).
Transcrição:
{texto}