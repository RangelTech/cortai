"""Banco do Cortaí: SQLite num volume (/data). Poucas tabelas, tudo com hora em epoch (s)."""
from __future__ import annotations

import os
import sqlite3
import uuid
import threading
import time
from contextlib import contextmanager

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  criado REAL NOT NULL,
  plano TEXT NOT NULL DEFAULT 'teste',      -- teste | mensal | anual
  ativo_ate REAL NOT NULL DEFAULT 0,          -- assinatura vale ate (epoch); 0 = sem assinatura
  pedido TEXT                                  -- ultimo pedido da Cakto
);
CREATE TABLE IF NOT EXISTS codigos (
  email TEXT PRIMARY KEY,
  hash TEXT NOT NULL,
  expira REAL NOT NULL,
  tentativas INTEGER NOT NULL DEFAULT 0,
  enviado REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessoes (
  hash TEXT PRIMARY KEY,
  usuario INTEGER NOT NULL,
  expira REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trabalhos (
  id TEXT PRIMARY KEY,
  usuario INTEGER NOT NULL,
  tipo TEXT NOT NULL,                          -- cortes | animacao | clonar
  entrada TEXT NOT NULL,                       -- JSON do pedido
  estado TEXT NOT NULL DEFAULT 'fila',         -- fila | rodando | pronto | erro
  etapa TEXT NOT NULL DEFAULT '',
  progresso REAL NOT NULL DEFAULT 0,
  resultado TEXT,                              -- JSON (cortes + relatorio)
  erro TEXT,
  criado REAL NOT NULL,
  fim REAL
);
CREATE INDEX IF NOT EXISTS trab_usuario ON trabalhos(usuario, criado);
CREATE TABLE IF NOT EXISTS eventos_cakto (
  id TEXT PRIMARY KEY,
  recebido REAL NOT NULL,
  corpo TEXT NOT NULL
);
"""


class BancoSQLite:
    def __init__(self, caminho: str):
        self.caminho = caminho
        self._trava = threading.Lock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(ESQUEMA)
        _migrar_esquema(self)

    @contextmanager
    def tx(self):
        with self._trava:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield self.con
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise

    def um(self, sql: str, args=()):
        with self._trava:
            r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    def todos(self, sql: str, args=()):
        with self._trava:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def exec(self, sql: str, args=()):
        with self._trava:
            return self.con.execute(sql, args)


def agora() -> float:
    return time.time()


def _migrar_esquema(self):
    """Migrações de coluna e tabela (rodam no SQLite e no Postgres; spec 57)."""
    cols = {r[1] for r in self.con.execute("PRAGMA table_info(usuarios)")}
    for col in ("arroba", "arroba_tiktok", "redes", "marca_cor"):  # perfil de marca (polimento 15/11)
        if col not in cols:
            self.con.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT")


def Banco(caminho: str):
    """Postgres central (banco `produtos`, schema do produto) quando PRODUTOS_DATABASE_URL existe; senão o SQLite de antes
    (plano de volta). Spec 57, seção 8. PRODUTOS_SCHEMA_TESTE=1 dá um schema novo por instância (testes isolados)."""
    dsn = os.environ.get("PRODUTOS_DATABASE_URL")
    if not dsn:
        return BancoSQLite(caminho)
    from produtos_core.banco_pg import Banco as BancoPG
    schema = os.environ.get("PRODUTOS_SCHEMA", "cortai")
    if os.environ.get("PRODUTOS_SCHEMA_TESTE"):
        schema = "teste_" + uuid.uuid4().hex[:10]
    b = BancoPG(dsn, schema, ESQUEMA)
    _migrar_esquema(b)
    return b
