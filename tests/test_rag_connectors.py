"""Unit konektor: secrets + helper murni + validasi konstruktor."""

import pytest

from app.infra.rag.connectors.common import (
    check_ident,
    check_readonly_sql,
    format_watermark,
    parse_watermark,
    render_row,
)
from app.infra.rag.connectors.mysql import MysqlSourceConnector
from app.infra.rag.connectors.postgres import PostgresSourceConnector
from app.infra.rag.secrets import decrypt_secret, encrypt_secret


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_secrets_roundtrip():
    key = _fernet_key()
    blob = encrypt_secret("p@ssw0rd-rahasia", key)
    assert isinstance(blob, bytes) and b"rahasia" not in blob
    assert decrypt_secret(blob, key) == "p@ssw0rd-rahasia"


def test_secrets_kunci_salah_gagal_jelas():
    blob = encrypt_secret("x", _fernet_key())
    with pytest.raises(ValueError, match="SOURCE_CONN_KEY"):
        decrypt_secret(blob, _fernet_key())


def test_ident_validasi():
    assert check_ident("orders_2") == "orders_2"
    assert check_ident("select") == "select"  # reserved word aman (quoted)
    for bad in ["orders; DROP", "a-b", "x.y", "", "a b"]:
        with pytest.raises(ValueError):
            check_ident(bad)


def test_denylist_tulis():
    check_readonly_sql("SELECT * FROM t WHERE id = %s")
    for bad in ["SELECT * FROM t; DELETE FROM t", "update t set x=1",
                "DROP TABLE t", "insert into t values (1)"]:
        with pytest.raises(ValueError, match="tulis"):
            check_readonly_sql(bad)


def test_watermark_parse_format():
    assert parse_watermark("") == ("1970-01-01T00:00:00+00:00", "")
    assert parse_watermark("2026-01-02T03:04:05+00:00|42") == \
        ("2026-01-02T03:04:05+00:00", "42")
    with pytest.raises(ValueError):
        parse_watermark("sampah")
    from datetime import datetime, timezone

    ts = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    assert format_watermark(ts, 7) == f"{ts.isoformat()}|7"


def test_render_row_lewati_null():
    out = render_row("orders", {"id": 1, "total": 5000, "note": None,
                                "kosong": "  "}, pk_cols=["id"])
    assert out == "[orders] id: 1 | total: 5000"


def test_konektor_tolak_read_only_false():
    with pytest.raises(ValueError, match="read_only"):
        PostgresSourceConnector("postgresql://x", read_only=False)
    with pytest.raises(ValueError, match="read_only"):
        MysqlSourceConnector(host="h", port=3306, db="d", user="u",
                             password="p", read_only=False)


def test_konektor_tolak_tabel_di_luar_allowlist():
    c = PostgresSourceConnector("postgresql://x", allowed_tables=["orders"])
    with pytest.raises(ValueError, match="allowlist"):
        c._check_table("users")
    assert c._check_table("orders") == "orders"


def test_proyeksi_wajib_watermark():
    c = PostgresSourceConnector("postgresql://x",
                                columns={"orders": ["id", "total"]})
    with pytest.raises(ValueError, match="watermark"):
        c._projection("orders", ["id", "total", "updated_at"])
