from app.observability import start_run
from app.observability.tracing import build_langchain_config, trace_user_id


def test_trace_user_id_uses_internal_id_only():
    assert trace_user_id(42) == "u_42"
    assert trace_user_id(None) == "anonymous"


def test_langchain_config_carries_langfuse_user_id():
    with start_run("quiz", user_id=trace_user_id(7)):
        cfg = build_langchain_config("quiz_generate")
    assert cfg["metadata"]["langfuse_user_id"] == "u_7"


def test_langchain_config_without_run_has_no_user():
    cfg = build_langchain_config("quiz_generate")
    assert "langfuse_user_id" not in cfg["metadata"]
