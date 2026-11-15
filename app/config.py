"""Configuração por variável de ambiente (segredos em /opt/platform/secrets/cortai.env na VPS)."""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field


@dataclass
class Config:
    dados: str = "/data"
    base_url: str = "https://cortai.rangeltech.net"
    llm_base: str = "https://qwen.rangeltech.net/v1"
    llm_key: str = ""
    llm_modelo: str = "qwen-abliterated"
    segredo_sessao: str = "dev"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_senha: str = ""
    smtp_from: str = "Cortaí <contato@rangeltech.net>"
    cakto_segredo: str = ""
    checkout_mensal: str = ""
    trabalhos_teste: int = 0          # sem plano grátis (decisão do Lucas, 14/11)
    trabalhos_mes: int = 60           # limite do plano mensal
    max_duracao_s: int = 3600         # vídeo de entrada: até 1 h
    max_cortes: int = 5
    workers: int = 1
    guardar_dias: int = 7
    proxy: str = ""                    # proxy para o yt-dlp (opcional)
    dev: bool = False
    checkouts: dict = field(default_factory=dict)          # {"start": url, "pro": url, "turbo": url}
    planos_por_produto: dict = field(default_factory=dict)  # {"<id do produto Cakto>": plano}
    lancamento_ate: str = "2026-11-30"
    admin_key: str = ""


def carregar() -> Config:
    e = os.environ.get
    return Config(
        dados=e("CORTAI_DADOS", "/data"),
        base_url=e("CORTAI_BASE_URL", "https://cortai.rangeltech.net").rstrip("/"),
        llm_base=e("CORTAI_LLM_BASE", "https://qwen.rangeltech.net/v1").rstrip("/"),
        llm_key=e("CORTAI_LLM_KEY", ""),
        llm_modelo=e("CORTAI_LLM_MODELO", "qwen-abliterated"),
        segredo_sessao=e("CORTAI_SEGREDO_SESSAO", "dev"),
        smtp_host=e("SMTP_HOST", ""),
        smtp_port=int(e("SMTP_PORT", "587") or 587),
        smtp_user=e("SMTP_USER", ""),
        smtp_senha=e("SMTP_PASSWORD", ""),
        smtp_from=e("CORTAI_SMTP_FROM", "Cortaí <contato@rangeltech.net>"),
        cakto_segredo=e("CORTAI_CAKTO_SEGREDO", ""),
        checkout_mensal=e("CORTAI_CHECKOUT_MENSAL", ""),
        trabalhos_teste=int(e("CORTAI_TRABALHOS_TESTE", "0")),
        trabalhos_mes=int(e("CORTAI_TRABALHOS_MES", "60")),
        max_duracao_s=int(e("CORTAI_MAX_DURACAO_S", "3600")),
        max_cortes=int(e("CORTAI_MAX_CORTES", "5")),
        workers=int(e("CORTAI_WORKERS", "1")),
        guardar_dias=int(e("CORTAI_GUARDAR_DIAS", "7")),
        proxy=e("CORTAI_PROXY", ""),
        dev=e("CORTAI_DEV", "") == "1",
        checkouts=json.loads(e("CORTAI_CHECKOUTS", "") or "{}"),
        planos_por_produto=json.loads(e("CORTAI_PLANOS_CAKTO", "") or "{}"),
        lancamento_ate=e("CORTAI_LANCAMENTO_ATE", "2026-11-30"),
        admin_key=e("CORTAI_ADMIN_KEY", ""),
    )
