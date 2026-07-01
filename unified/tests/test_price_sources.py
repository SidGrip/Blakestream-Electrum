"""Orchestrator price-source config: persistence, key masking, validation, reorder/remove.

Skipped where the electrum runtime isn't installed (the orchestrator imports provisioning ->
electrum_ecc); runs on the build/CI env. Network is monkeypatched out — these exercise config
plumbing, not real fetches."""

import pytest

pytest.importorskip("electrum_ecc")  # orchestrator -> provisioning -> electrum_ecc

from unified import prices                                    # noqa: E402
from unified.orchestrator import Orchestrator, DEFAULT_RPC_PORTS  # noqa: E402


def _orch(dd, monkeypatch):
    monkeypatch.setattr(prices, "safe_fetch_json", lambda *a, **k: None)  # no real network
    return Orchestrator(python_bin="python3", workspaces_root=dd, datadirs_root=dd,
                        servers={}, coins={t: {"ticker": t} for t in DEFAULT_RPC_PORTS})


def test_price_sources_persist_and_mask(tmp_path, monkeypatch):
    dd = str(tmp_path)
    o = _orch(dd, monkeypatch)
    pub = o.price_sources_public()
    assert pub["enabled"] is False
    assert set(pub["tickers"]) == set(DEFAULT_RPC_PORTS)

    assert pub["sources"] == []   # nothing shipped — fully user-defined

    o.add_price_source({"role": "coin_fiat", "kind": "http_template",
                        "urlTemplate": "https://x/p?coin={coin}&fiat={fiat}", "jsonPath": "price",
                        "apiKeyHeader": "X-Key", "apiKey": "supersecret", "label": "mine"})
    o.set_display_prefs("EUR", True)
    o.set_price_enabled(True)

    pub = o.price_sources_public()
    assert pub["display"] == {"fiatCurrency": "EUR", "displayFiat": True}
    src = next(s for s in pub["sources"] if s["label"] == "mine")
    assert src["hasApiKey"] and src["apiKeyMask"].endswith("cret")
    assert "supersecret" not in str(pub)     # raw key never appears in the masked view

    # persisted across a fresh load from the sidecar
    p2 = _orch(dd, monkeypatch).price_sources_public()
    assert p2["enabled"] and p2["display"]["fiatCurrency"] == "EUR"
    # the raw key survives reload (still usable) but is still masked in the public view
    src2 = next(s for s in p2["sources"] if s["label"] == "mine")
    assert src2["hasApiKey"] and "supersecret" not in str(p2)


def test_price_source_validation(tmp_path, monkeypatch):
    o = _orch(str(tmp_path), monkeypatch)
    with pytest.raises(Exception):
        o.add_price_source({"role": "bogus", "kind": "http_template",
                            "urlTemplate": "https://x", "jsonPath": "p"})
    with pytest.raises(Exception):
        o.add_price_source({"role": "coin_btc", "kind": "http_template",
                            "urlTemplate": "https://x/{evil}", "jsonPath": "p"})   # stray placeholder
    with pytest.raises(Exception):
        o.add_price_source({"role": "coin_btc", "kind": "manual",                  # kind removed
                            "urlTemplate": "", "jsonPath": ""})
    with pytest.raises(Exception):
        o.set_display_prefs("EURO", None)            # not a 3-letter code


def test_update_keeps_key_unless_cleared(tmp_path, monkeypatch):
    o = _orch(str(tmp_path), monkeypatch)
    pub = o.add_price_source({"role": "coin_fiat", "kind": "http_template",
                              "urlTemplate": "https://x/p?c={coin}", "jsonPath": "price",
                              "apiKeyHeader": "X-Key", "apiKey": "k123456", "label": "S"})
    sid = next(s["id"] for s in pub["sources"] if s["label"] == "S")
    # update without an apiKey -> existing key retained
    pub = o.update_price_source(sid, {"role": "coin_fiat", "kind": "http_template",
                                      "urlTemplate": "https://x/p?c={coin}", "jsonPath": "price",
                                      "apiKeyHeader": "X-Key", "label": "S2"})
    src = next(s for s in pub["sources"] if s["id"] == sid)
    assert src["label"] == "S2" and src["hasApiKey"]
    # explicit clear -> key removed
    pub = o.update_price_source(sid, {"role": "coin_fiat", "kind": "http_template",
                                      "urlTemplate": "https://x/p?c={coin}", "jsonPath": "price",
                                      "apiKeyHeader": "X-Key", "label": "S2", "clearApiKey": True})
    src = next(s for s in pub["sources"] if s["id"] == sid)
    assert not src["hasApiKey"]


def test_reorder_and_remove(tmp_path, monkeypatch):
    o = _orch(str(tmp_path), monkeypatch)
    pub = o.add_price_source({"role": "coin_btc", "kind": "http_template",
                              "urlTemplate": "https://x/a", "jsonPath": "p", "label": "A"})
    ids = [s["id"] for s in pub["sources"]]
    o.reorder_price_sources(list(reversed(ids)))
    after = o.price_sources_public()["sources"]
    assert [s["id"] for s in after] == list(reversed(ids))
    last = after[-1]["id"]
    pub = o.remove_price_source(last)
    assert last not in [s["id"] for s in pub["sources"]]
