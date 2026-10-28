"""Provider-mode tests with intercepted transport and stubbed worker execution."""
import hashlib
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_drill
import agent_adapter
from fixtures import get_target_file, has_task_creation_defect
from isolated_worker import IsolatedWorkerController, IsolatedWorkerSession


@pytest.fixture
def worker_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_adapter, "is_isolated_worker_available", lambda: True)
    session = MagicMock(staging_dir=tmp_path / "staging", session_id="stub-session")
    context = MagicMock()
    context.__enter__.return_value = session
    monkeypatch.setattr(IsolatedWorkerSession, "create", lambda *args, **kwargs: context)
    monkeypatch.setattr(IsolatedWorkerController, "run_isolated_pytest", lambda *args, **kwargs: (True, "stub"))
    calls = []
    def functional(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return False, {"normal_status": 201, "urgent_status": 500}, "stub"
        return True, {"normal_status": 201, "urgent_status": 201, "urgent_persisted": True, "db_verified": True}, "stub"
    monkeypatch.setattr(IsolatedWorkerController, "run_isolated_functional_verification", functional)
    return calls


def test_live_diff_from_intercepted_provider_is_reviewed_before_application(monkeypatch, worker_stub):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    received = []
    def transport(self, request, timeout):
        payload = json.loads(request.data)
        prompt = payload["messages"][1]["content"]
        source = prompt.split("```python\n", 1)[1].split("\n```", 1)[0]
        diff = model_drill.generate_synthetic_remediation_diff(source + "\n")
        received.append(diff)
        body = json.dumps({"choices": [{"message": {"content": diff}}]}).encode()
        return io.BytesIO(body)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", transport)
    reviewed = []
    def review(proposal):
        assert has_task_creation_defect(Path(proposal["workspace_path"]))
        assert proposal["diff"] == received[0]
        assert proposal["diff_sha256"] == hashlib.sha256(received[0].encode()).hexdigest()
        reviewed.append(proposal["diff"])
        return True  # Simulates a decision; not evidence of a human reviewer.
    result = model_drill.run_controlled_model_drill(mode=model_drill.MODE_LIVE_PROVIDER, review_callback=review)
    assert result["success"] is True
    assert result["execution_mode"] == model_drill.MODE_LIVE_PROVIDER
    assert result["stages"]["stage_3_model_proposal"]["model_executed"] is True
    assert reviewed == received and len(received) == 1
    assert len(worker_stub) == 3


@pytest.mark.parametrize("kind", ["network", "invalid", "malformed"])
def test_live_provider_errors_never_reach_review_or_application(monkeypatch, worker_stub, kind):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    called = []
    def transport(self, request, timeout):
        called.append(True)
        if kind == "network":
            raise urllib.error.URLError("offline-test")
        content = b"not json" if kind == "malformed" else json.dumps({"choices": [{"message": {"content": "not a diff"}}]}).encode()
        return io.BytesIO(content)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", transport)
    def forbidden(*args, **kwargs):
        raise AssertionError("review or application must not run")
    monkeypatch.setattr(model_drill, "apply_human_reviewed_patch", forbidden)
    with pytest.raises(RuntimeError, match="Live proposal failed"):
        model_drill.run_controlled_model_drill(mode=model_drill.MODE_LIVE_PROVIDER, review_callback=forbidden)
    assert len(called) == 1
    assert len(worker_stub) == 1


def test_offline_mode_cannot_use_transport_even_with_keys(monkeypatch, worker_stub):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    def forbidden(*args, **kwargs):
        raise AssertionError("offline mode accessed transport")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    result = model_drill.run_controlled_model_drill(api_key="dummy-argument", review_callback=lambda proposal: False)
    assert result["execution_mode"] == model_drill.MODE_OFFLINE_SIMULATED
    assert result["stages"]["stage_3_model_proposal"]["model_executed"] is False
    assert result["stages"]["stage_5_after_http"] == {"status": "not_run"}
    assert len(worker_stub) == 2


def test_live_withheld_review_does_not_apply(monkeypatch, worker_stub):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    def transport(self, request, timeout):
        prompt = json.loads(request.data)["messages"][1]["content"]
        source = prompt.split("```python\n", 1)[1].split("\n```", 1)[0]
        diff = model_drill.generate_synthetic_remediation_diff(source + "\n")
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": diff}}]}).encode())
    monkeypatch.setattr("urllib.request.OpenerDirector.open", transport)
    def review(proposal):
        assert has_task_creation_defect(Path(proposal["workspace_path"]))
        return False
    def forbidden(*args, **kwargs):
        raise AssertionError("patch applied without review")
    monkeypatch.setattr(model_drill, "apply_human_reviewed_patch", forbidden)
    result = model_drill.run_controlled_model_drill(mode=model_drill.MODE_LIVE_PROVIDER, review_callback=review)
    assert result["success"] is False
    assert result["stages"]["stage_5_after_http"] == {"status": "not_run"}
    assert len(worker_stub) == 2


def test_live_without_key_fails_without_transport(monkeypatch, worker_stub):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("transport or review called without a key")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    with pytest.raises(RuntimeError, match="api_key_missing"):
        model_drill.run_controlled_model_drill(mode=model_drill.MODE_LIVE_PROVIDER, review_callback=forbidden)
    assert len(worker_stub) == 1
