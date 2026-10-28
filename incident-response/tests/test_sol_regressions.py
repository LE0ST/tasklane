import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from model_drill import create_approval_ticket, run_controlled_model_drill
from isolated_worker import IsolatedWorkerSession


def test_ticket_requires_literal_true():
    assert create_approval_ticket({}, confirmed=1)['confirmed'] is False


def test_drill_never_accepts_preapproval():
    with pytest.raises((TypeError, ValueError, PermissionError)):
        run_controlled_model_drill(human_approved=True)


def test_recovery_is_non_destructive(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('automatic recovery must not remove containers')
    monkeypatch.setattr(IsolatedWorkerSession, '_remove_docker_container', forbidden)
    result = IsolatedWorkerSession.recover_orphaned_containers()
    assert result['status'] != 'all_cleaned'



def test_offline_never_calls_provider_and_waits_for_review(monkeypatch):
    import model_drill
    from sandbox import create_disposable_workspace, cleanup_disposable_workspace
    from fixtures import inject_task_creation_defect, get_target_file
    from unittest.mock import patch
    repo = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo, 'offline-no-provider')
    try:
        inject_task_creation_defect(ws)
        original = get_target_file(ws).read_bytes()
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-used')
        with patch.object(model_drill.DeepSeekAgentAdapter, '_call_api', side_effect=AssertionError('provider called')):
            # Force a missing worker: proposal must still avoid any provider access.
            with patch('agent_adapter.is_isolated_worker_available', return_value=False):
                proposal = model_drill.create_remediation_proposal('offline-no-provider', ws, api_key='must-not-be-used')
        assert proposal['diff']
        assert get_target_file(ws).read_bytes() == original
        ticket = model_drill.create_approval_ticket(proposal, confirmed=1)
        with pytest.raises(model_drill.HumanReviewRejectedError):
            model_drill.apply_human_reviewed_patch(ws, proposal['diff'], ticket)
        assert get_target_file(ws).read_bytes() == original
    finally:
        cleanup_disposable_workspace(ws, 'offline-no-provider')


def test_unregistered_incident_rejected():
    from sandbox import create_disposable_workspace, cleanup_disposable_workspace, SecurityViolation
    from model_drill import create_remediation_proposal
    repo = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo, 'registered-incident')
    try:
        with pytest.raises((SecurityViolation, PermissionError)):
            create_remediation_proposal('different-incident', ws)
    finally:
        cleanup_disposable_workspace(ws, 'registered-incident')


def test_functional_failure_keeps_pytest_advisory(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from agent_adapter import DeepSeekAgentAdapter
    from isolated_worker import IsolatedWorkerController, IsolatedWorkerSession
    from sandbox import create_disposable_workspace, cleanup_disposable_workspace
    from fixtures import inject_task_creation_defect, get_target_file
    from model_drill import generate_synthetic_remediation_diff
    repo = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo, 'functional-failure')
    try:
        inject_task_creation_defect(ws)
        diff = generate_synthetic_remediation_diff(get_target_file(ws).read_text(encoding='utf-8'))
        adapter = DeepSeekAgentAdapter(api_key='dummy')
        adapter.write_capable_enabled = True
        monkeypatch.setattr(adapter, '_call_api', lambda **kwargs: (diff, None))
        monkeypatch.setattr('agent_adapter.is_isolated_worker_available', lambda: True)
        session = MagicMock()
        session.staging_dir = tmp_path
        context = MagicMock()
        context.__enter__.return_value = session
        monkeypatch.setattr(IsolatedWorkerSession, 'create', lambda *args: context)
        monkeypatch.setattr(IsolatedWorkerController, 'run_isolated_pytest', lambda *args, **kwargs: (True, 'passed'))
        monkeypatch.setattr(IsolatedWorkerController, 'run_isolated_functional_verification', lambda *args, **kwargs: (False, {}, 'failed'))
        result = adapter.repair({}, ws, operator_approved=True)
        assert result['status'] == 'repair_failed'
        assert result['tests_passed'] is False
        assert result['tests_passed_advisory'] is True
    finally:
        cleanup_disposable_workspace(ws, 'functional-failure')


def test_revoked_registration_rejects_approval(monkeypatch):
    from unittest.mock import patch
    from sandbox import create_disposable_workspace, cleanup_disposable_workspace, unregister_workspace
    from fixtures import inject_task_creation_defect, get_target_file
    import model_drill
    repo = Path(__file__).resolve().parents[2]
    ws = create_disposable_workspace(repo, 'revoked-approval')
    try:
        inject_task_creation_defect(ws)
        with patch('agent_adapter.is_isolated_worker_available', return_value=False):
            proposal = model_drill.create_remediation_proposal('revoked-approval', ws)
        ticket = model_drill.create_approval_ticket(proposal, confirmed=True)
        before = get_target_file(ws).read_bytes()
        unregister_workspace('revoked-approval')
        with pytest.raises(PermissionError):
            model_drill.apply_human_reviewed_patch(ws, proposal['diff'], ticket)
        assert get_target_file(ws).read_bytes() == before
    finally:
        cleanup_disposable_workspace(ws, 'revoked-approval')
