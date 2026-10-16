import importlib
import pytest
from fastapi.testclient import TestClient

def test_health_and_readiness(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_ENV','development')
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'portable.sqlite'))
    monkeypatch.setenv('MATRIX_EXEC_KEY','unit-key')
    import app.main
    m=importlib.reload(app.main)
    with TestClient(m.app) as api:
        assert api.get('/health').json()['live_sending'] is False
        assert api.get('/ready').json()['status']=='ready'
        assert api.get('/console').status_code==200

def test_staging_requires_secret(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_ENV','staging')
    monkeypatch.setenv('MATRIX_EXEC_KEY','weak')
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'stage.sqlite'))
    import app.main
    m=importlib.reload(app.main)
    with pytest.raises(RuntimeError,match='Staging requires'):
        with TestClient(m.app): pass

def test_production_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('MATRIX_ENV','production')
    monkeypatch.setenv('MATRIX_EXEC_KEY','a'*40)
    monkeypatch.setenv('MATRIX_EXEC_DB',str(tmp_path/'prod.sqlite'))
    import app.main
    m=importlib.reload(app.main)
    with pytest.raises(RuntimeError,match='Production is blocked'):
        with TestClient(m.app): pass
