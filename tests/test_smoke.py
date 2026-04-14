from server import app, DeployState


def test_app_boots():
    assert app.title == "Pipeline Gatekeeper"


def test_deploy_states_defined():
    assert DeployState.PENDING == "pending"
    assert DeployState.APPROVED == "approved"
    assert DeployState.ROLLED_BACK == "rolled_back"
