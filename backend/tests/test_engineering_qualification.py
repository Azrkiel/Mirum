"""Qualification tools enforce fixed corpus, literal credentials and budgets."""
import json
import hashlib
import pytest
from scripts.checked_engineering_live_eval import load_credential,CORPUS_SHA256


def test_only_literal_existing_gemini_key_is_loaded(tmp_path,monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY',raising=False)
    monkeypatch.delenv('OTHER_SECRET',raising=False)
    path=tmp_path/'provider.env'
    path.write_text('OTHER_SECRET=private\nGEMINI_API_KEY="fixture-key.stage" # comment\nGEMINI_API_KEYS=ignored-pool\n')
    assert load_credential(path)=='GEMINI_API_KEY'
    import os
    assert os.environ['GEMINI_API_KEY_HEAVY']=='fixture-key.stage'
    assert 'OTHER_SECRET' not in os.environ and 'GEMINI_API_KEYS' not in os.environ
    path.write_text('GEMINI_API_KEY=$(do_not_execute)')
    with pytest.raises(ValueError): load_credential(path)


def test_live_corpus_identity_and_category_counts_are_fixed():
    from pathlib import Path
    from collections import Counter
    raw=Path('backend/tests/fixtures/engineering/ai-corpus-v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==CORPUS_SHA256
    assert Counter(c['kind'] for c in json.loads(raw)['cases'])==dict(complete=10,ambiguous=10,unsupported=10)
