# Cortaí

Site de cortes virais (spec 38 B do ecossistema Rangel Tech): cole o link de um vídeo e receba cortes verticais com legenda, gancho e relatório.

- `app/`: FastAPI (login por código no e-mail, fila de trabalhos, webhook da Cakto) e o pipeline (yt-dlp, transcrição pelo llm-access, escolha dos trechos pela IA, ffmpeg).
- `static/`: página de vendas e painel.
- Deploy: somente pelo `RangelTech/vps_rt_infra` (stack `cortai`). Release: workflow `Release` com a versão.
- Segredos: `/opt/platform/secrets/cortai.env` na VPS (`CORTAI_LLM_KEY`, `CORTAI_SEGREDO_SESSAO`, SMTP, Cakto).

Teste local: `CORTAI_DEV=1 CORTAI_DADOS=./dados uvicorn app.main:app` (o código de login aparece na resposta).
