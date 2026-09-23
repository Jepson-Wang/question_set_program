from backend.agents.rag.config import RagSettings
from backend.core.config import BACKEND_ROOT

def test_relative_dirs_resolve_against_backend_root(tmp_path,monkeypatch):
    """与 core/config 同一原则：从哪个目录启动都不影响读写位置。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_DB_DIR","my_rag")
    assert RagSettings.from_env().db_dir == (BACKEND_ROOT/"my_rag").resolve()

def test_absolute_dir_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB_DIR",str(tmp_path/"abs"))
    assert RagSettings.from_env().db_dir == tmp_path / "abs"

def test_defaults(monkeypatch):
    for key in ("RAG_DB_DIR", "RAG_UPLOAD_DIR", "RAG_EVAL_DIR", "RAG_ENABLED"):
        monkeypatch.delenv(key,raising = False)
    s = RagSettings.from_env()
    assert s.enabled is True
    assert s.db_dir == (BACKEND_ROOT/"rag_db").resolve()
    assert (s.route_top_k, s.fusion_top_n, s.final_top_k, s.knowledge_top_k) == (20, 30, 3, 2)
    assert s.rrf_k == 60
    assert s.near_dup_threshold == s.leak_threshold == 0.95

def test_enabled_flag_parsing(monkeypatch):
    for raw,expected in [("false",False),("0",False),("true",True),("ON",True)]:
        monkeypatch.setenv("RAG_ENABLED",raw)
        assert RagSettings.from_env().enabled is expected

def test_rerank_key_falls_back_to_api_key(monkeypatch):
    monkeypatch.delenv("RERANK_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "k-main")
    assert RagSettings.from_env().rerank_api_key == "k-main"
