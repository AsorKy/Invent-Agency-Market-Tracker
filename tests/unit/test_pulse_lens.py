"""The pulse socioeconomic refresh accepts only its own caller (T-189, REQ-SEC-005).

T-185 mints a 60 second token for the fixed caller ``n8n_socioeco``. The refresh route must
refuse a validly signed token of any other service. The other pulse routes are untouched
(``test_pulse_lens_routes.py`` covers them).
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("fastapi")
pytest.importorskip("structlog")
pytest.importorskip("sqlalchemy")
testclient = pytest.importorskip("fastapi.testclient")

from libs.common.internal_auth import sign_service_token  # noqa: E402
from libs.common.settings import get_settings  # noqa: E402
from services.pulse.app import main as pulse  # noqa: E402
from tests.unit.service_auth import service_headers  # noqa: E402

PATH = "/internal/pulse/socioeconomic/refresh"


@pytest.fixture
def client(tmp_path: Path) -> Any:
    pulse.app.state.repository = type("R", (), {"socio_data_dir": str(tmp_path / "none")})()
    return testclient.TestClient(pulse.app)


def test_the_n8n_socioeco_token_is_accepted(client: Any) -> None:
    response = client.post(PATH, json={}, headers=service_headers("n8n_socioeco"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refreshed"


@pytest.mark.parametrize("service", ["gateway", "seed", "verify_live", "ml", "brief"])
def test_a_validly_signed_token_of_another_service_is_refused(client: Any, service: str) -> None:
    response = client.post(PATH, json={}, headers=service_headers(service))
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_service_token"


def test_a_missing_token_is_refused(client: Any) -> None:
    assert client.post(PATH, json={}).status_code == 401


def test_a_token_signed_with_another_key_is_refused(client: Any) -> None:
    forged = sign_service_token("n8n_socioeco", get_settings().internal_service_token + "-other")
    assert client.post(PATH, json={}, headers={"X-Service-Token": forged}).status_code == 401
