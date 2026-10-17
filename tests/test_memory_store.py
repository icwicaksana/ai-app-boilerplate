"""MemoryStore + heuristik memori (tanpa DB — fake store dict-backed)."""

import pytest

from app.agents.BaseChatAgent import extract_memory_writes
from app.infra.memory import (
    MAX_FACT_CHARS,
    MemoryStore,
    looks_like_secret,
    namespace_for,
    profile_to_prompt_text,
)


class _Item:
    def __init__(self, value):
        self.value = value


class FakeRawStore:
    """Meniru subset AsyncPostgresStore: aput/aget/asearch."""

    def __init__(self):
        self.data: dict = {}

    async def aput(self, namespace, key, value):
        self.data[(tuple(namespace), key)] = dict(value)

    async def aget(self, namespace, key):
        value = self.data.get((tuple(namespace), key))
        return _Item(dict(value)) if value is not None else None

    async def asearch(self, namespace, query="", limit=5):
        ns = tuple(namespace)
        out = []
        for (n, _k), v in self.data.items():
            if n == ns and isinstance(v, dict) and query.lower() in str(v).lower():
                out.append(_Item(dict(v)))
        return out[:limit]


class BoomStore(FakeRawStore):
    async def aput(self, *a, **k):
        raise ConnectionError("db mati")

    async def aget(self, *a, **k):
        raise ConnectionError("db mati")

    async def asearch(self, *a, **k):
        raise ConnectionError("db mati")


class FlakyReadStore(FakeRawStore):
    """Baca gagal, tulis sukses — pemicu bug merge-destruktif (P1 review)."""

    def __init__(self):
        super().__init__()
        self.writes: list = []

    async def aget(self, *a, **k):
        raise ConnectionError("baca gagal transien")

    async def aput(self, namespace, key, value):
        self.writes.append((tuple(namespace), key, dict(value)))
        await super().aput(namespace, key, value)


def _mem(raw=None):
    return MemoryStore(raw or FakeRawStore(), "toko-a", "budi")


def test_namespace_canonical():
    assert namespace_for("toko-a", "budi") == ("tenants", "toko-a", "users", "budi")
    assert namespace_for("Toko-A", "Budi") == ("tenants", "toko-a", "users", "budi")
    assert _mem().namespace == ("tenants", "toko-a", "users", "budi")


@pytest.mark.asyncio
async def test_profile_merge_not_overwrite():
    mem = _mem()
    await mem.put_profile({"nama": "Budi"})
    final = await mem.put_profile({"diet": "vegetarian"})
    assert final == {"nama": "Budi", "diet": "vegetarian"}
    assert await mem.get_profile() == final


@pytest.mark.asyncio
async def test_profile_empty_default():
    assert await _mem().get_profile() == {}


@pytest.mark.asyncio
async def test_put_fact_key_and_caps():
    mem = _mem()
    key = await mem.put_fact("saya vegetarian")
    assert key.startswith("fact:")
    with pytest.raises(ValueError):
        await mem.put_fact("   ")
    long_key = await mem.put_fact("x" * (MAX_FACT_CHARS + 100))
    item = await mem._store.aget(mem.namespace, long_key)
    assert len(item.value["text"]) == MAX_FACT_CHARS


@pytest.mark.asyncio
async def test_fail_soft_read_paths():
    mem = _mem(BoomStore())
    assert await mem.get_profile() == {}
    assert await mem.search_facts("apa pun") == []


@pytest.mark.asyncio
async def test_fail_loud_write_paths():
    mem = _mem(BoomStore())
    with pytest.raises(ConnectionError):
        await mem.put_profile({"a": "b"})
    with pytest.raises(ConnectionError):
        await mem.put_fact("coba")


@pytest.mark.asyncio
async def test_put_profile_never_merges_over_failed_read():
    """Baca gagal + tulis bisa → raise TANPA tulis (cengkeraman P1)."""
    raw = FlakyReadStore()
    await FakeRawStore.aput(raw, ("tenants", "toko-a", "users", "budi"),
                            "profile", {"nama": "Budi"})
    mem = _mem(raw)
    with pytest.raises(ConnectionError):
        await mem.put_profile({"diet": "vegetarian"})
    assert raw.writes == []  # profil lama utuh, tak tertimpa patch saja


@pytest.mark.asyncio
async def test_search_filters_text_only():
    raw = FakeRawStore()
    mem = _mem(raw)
    await raw.aput(mem.namespace, "fact:1", {"text": "punya toko di Bandung"})
    await raw.aput(mem.namespace, PROFILE_KEY := "profile", {"nama": "Budi"})
    await raw.aput(mem.namespace, "junk", {"nope": 1})
    found = await mem.search_facts("toko")
    assert found == ["punya toko di Bandung"]


def test_profile_to_prompt_text():
    assert profile_to_prompt_text({}) == ""
    text = profile_to_prompt_text({"nama": "Budi", "diet": "vegetarian", "kosong": ""})
    assert "nama: Budi" in text and "diet: vegetarian" in text
    assert "kosong" not in text
    assert len(profile_to_prompt_text({"k": "x" * 2000})) <= 1000 + 30


def test_profile_to_prompt_text_with_facts():
    text = profile_to_prompt_text({"nama": "Budi"}, ["punya toko di Bandung"])
    assert "nama: Budi" in text and "Fakta: punya toko di Bandung" in text
    assert profile_to_prompt_text({}, ["sendirian"]) == \
        "Profil user tersimpan: Fakta: sendirian"


@pytest.mark.parametrize("text,expected", [
    ("password saya 123456", True),
    ("kata sandi: abc", True),
    ("my pass word is x", True),
    ("OTP 483921", True),
    ("PIN ATM 112233", True),
    ("PIN 112233", True),
    ("nomor kartu 4111 1111 1111 1111", True),
    ("card number 4111111111111111", True),
    ("cvv 123", True),
    ("saya vegetarian", False),
    ("buka pintu toko jam 8", False),  # "pin" dalam kata ≠ PIN
    ("opini saya bagus", False),
    ("", False),
])
def test_looks_like_secret(text, expected):
    assert looks_like_secret(text) is expected


@pytest.mark.parametrize("text,profile,facts", [
    ("ingat ya: saya vegetarian", {}, ["saya vegetarian"]),
    ("Tolong ingatkan saya minum obat jam 7", {}, ["saya minum obat jam 7"]),
    ("nama saya Budi", {"nama": "Budi"}, []),
    ("panggil aku Udin ya", {"nama": "Udin"}, []),
    ("nama saya Budi, ingat ya: saya vegetarian",
     {"nama": "Budi"}, ["saya vegetarian"]),
    ("halo, apa kabar?", {}, []),
    ("ingatkan", {}, []),  # terlalu pendek
    ("ingat ya: password saya 123456", {}, []),  # rahasia dibuang
    ("ingat ya: PIN saya 112233", {}, []),
    ("", {}, []),
])
def test_extract_memory_writes(text, profile, facts):
    assert extract_memory_writes(text) == {"profile": profile, "facts": facts}


def test_extract_memory_writes_non_string():
    assert extract_memory_writes(None) == {"profile": {}, "facts": []}
    assert extract_memory_writes({"a": 1}) == {"profile": {}, "facts": []}
