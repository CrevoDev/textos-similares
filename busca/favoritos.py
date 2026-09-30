"""CRUD simples da tabela `favoritos` — usado pelo app de busca para os
botões gostei/não gostei e para listar/desmarcar favoritos."""

from datetime import datetime, timezone

from config import DB_PATH
from db import conectar


def marcar(texto_id: str, tipo: str, db_path: str = DB_PATH) -> None:
    assert tipo in ("gostei", "nao_gostei")
    with conectar(db_path) as conn:
        conn.execute(
            "INSERT INTO favoritos (conto_id, tipo, marcado_em) VALUES (?, ?, ?) "
            "ON CONFLICT(conto_id) DO UPDATE SET tipo = excluded.tipo, marcado_em = excluded.marcado_em",
            (texto_id, tipo, datetime.now(timezone.utc).isoformat()),
        )


def desmarcar(texto_id: str, db_path: str = DB_PATH) -> None:
    with conectar(db_path) as conn:
        conn.execute("DELETE FROM favoritos WHERE conto_id = ?", (texto_id,))


def obter(texto_id: str, db_path: str = DB_PATH) -> str | None:
    with conectar(db_path) as conn:
        linha = conn.execute("SELECT tipo FROM favoritos WHERE conto_id = ?", (texto_id,)).fetchone()
    return linha["tipo"] if linha else None


def mapa(db_path: str = DB_PATH) -> dict[str, str]:
    """texto_id -> tipo de todos os favoritos, numa consulta só — o app usa
    isso pra pintar os botões de cada card sem uma ida ao banco por card."""
    with conectar(db_path) as conn:
        return {linha["conto_id"]: linha["tipo"] for linha in conn.execute("SELECT conto_id, tipo FROM favoritos")}


def listar(db_path: str = DB_PATH, tipo: str | None = None) -> list[dict]:
    from busca.catalogo import CAMPOS, JOIN_SERIE

    filtro = "WHERE f.tipo = ? " if tipo else ""
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT f.conto_id, f.tipo, f.marcado_em, {CAMPOS} "
            f"FROM favoritos f JOIN contos c ON c.id = f.conto_id {JOIN_SERIE} {filtro}"
            "ORDER BY f.marcado_em DESC",
            (tipo,) if tipo else (),
        ).fetchall()
    return [dict(linha) for linha in linhas]
