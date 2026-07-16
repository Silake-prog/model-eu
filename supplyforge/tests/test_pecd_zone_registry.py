"""Tests for the PECD per-zone regional registry (data-derived)."""
from supplyforge.fetch.pecd import zone_registry as zr


def test_levels_present():
    assert set(zr.levels()) == {"szon", "peon", "peof", "p2on", "p2of"}


def test_zones_for_known_countries():
    assert zr.zones_for("FR", "peon") == [f"FR{n:02d}" for n in range(1, 16)]   # FR01..FR15
    assert len(zr.zones_for("FR", "p2on")) == 26
    assert zr.zones_for("GR", "szon") == ["GR00", "GR03"]                         # GR, not EL
    assert zr.zones_for("DE", "szon") == ["DE00"]
    assert "FR081_OFF" in zr.zones_for("FR", "peof")


def test_zones_for_unknown_returns_empty():
    assert zr.zones_for("ZZ", "peon") == []
    assert zr.zones_for("FR", "not_a_level") == []


def test_countries_listing():
    assert "FR" in zr.countries("peon")
    assert "FR" in zr.countries("p2of")          # FR has offshore in ERAA-2026
    assert "AT" not in zr.countries("peof")      # landlocked: no offshore zones


def test_template_zone_capacities_skeleton():
    t = zr.template_zone_capacities("DE", "peon")
    assert set(t) == set(zr.zones_for("DE", "peon"))
    assert all(v is None for v in t.values())


def test_feasibility_note_documents_placeholders():
    note = zr.FEASIBILITY_NOTE.lower()
    assert "peof" in note and "placeholder" in note
