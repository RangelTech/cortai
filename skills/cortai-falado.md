---
name: cortai-falado
description: prompt-modelo de cortai/app/falado.py (PROMPT). Mantenha os campos entre chaves.
---
Você edita Reels. Abaixo está a fala de um vídeo curto gravado pela própria pessoa.
Escreva em português do Brasil:
- "titulo": título curto (até 60 letras)
- "gancho": texto para o topo do vídeo nos 3 primeiros segundos (até 40 letras, MAIÚSCULAS), fiel ao que é dito
- "legenda": legenda do post com chamada para comentar
- "hashtags": 5 hashtags do nicho
{repertorio}
Responda SÓ com JSON {{"titulo": "", "gancho": "", "legenda": "", "hashtags": []}}.

Fala:
{fala}