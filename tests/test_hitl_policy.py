"""Unit: policy HITL + hash aksi/versi + hash chain audit (murni)."""

import pytest

from app.core.hitl import (
    HITL_POLICY,
    ToolPolicy,
    action_hash,
    canonical_json,
    policy_for,
    policy_version,
)
from app.infra.approvals import GENESIS, compute_hash


def test_threshold_policy_semantics():
    pol = ToolPolicy(amount_field="amount", threshold=500_000)
    assert pol.requires_approval({"amount": 500_001}) is True
    assert pol.requires_approval({"amount": 500_000}) is False  # batas = lolos
    assert pol.requires_approval({"amount": 100}) is False
    assert pol.requires_approval({}) is False
    # argumen rusak → anggap berisiko
    assert pol.requires_approval({"amount": "bukan-angka"}) is True


def test_policy_without_threshold_requires_approval():
    pol = ToolPolicy()
    assert pol.requires_approval({"apa": "saja"}) is True


def test_disabled_policy_never_requires():
    assert ToolPolicy(enabled=False).requires_approval({"amount": 10**9}) is False


def test_default_policy_covers_refund():
    assert "process_refund" in HITL_POLICY
    assert policy_for("process_refund").threshold == 500_000
    assert policy_for("check_stock") is None


def test_action_hash_stable_and_sensitive():
    a = action_hash("process_refund", {"amount": 750_000, "order_id": "ORD-1"})
    b = action_hash("process_refund", {"order_id": "ORD-1", "amount": 750_000})
    c = action_hash("process_refund", {"amount": 750_001, "order_id": "ORD-1"})
    assert a == b          # urutan key tidak berpengaruh
    assert a != c          # nominal berubah → hash berubah (TOCTOU guard)
    assert len(a) == 64


def test_policy_version_stable_and_short():
    v = policy_version()
    assert v == policy_version()
    assert len(v) == 16


def test_canonical_json_deterministic():
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_audit_hash_chain_detects_tampering():
    body1 = {"tenant": "t", "actor": "budi", "action": "approval_requested",
             "subject_type": "approval", "subject_id": "1",
             "payload": {"x": 1}, "request_id": "-"}
    h1 = compute_hash(GENESIS, body1)
    body2 = {**body1, "action": "approval_decided", "subject_id": "1"}
    h2 = compute_hash(h1, body2)

    assert h1 == compute_hash(GENESIS, body1)          # deterministik
    assert h2 != compute_hash(GENESIS, body2)          # terikat prev
    tampered = {**body1, "payload": {"x": 2}}
    assert compute_hash(GENESIS, tampered) != h1       # perubahan isi terdeteksi
