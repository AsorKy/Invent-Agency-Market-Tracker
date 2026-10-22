"""Serve the score explanation consumed by the Brief (REQ-LNS-007, T-151).

``GET /internal/ml/explanations/{company_id}`` rebuilds ``drivers`` and the
``counterfactual`` from the features and weights stored on ``ml.lead_score`` by
``MlRepository.upsert_score``, using the pure functions of ``explain.py``. The
route accepts signed tokens of the service ``brief`` only and reads only the ml
schema (INV-01). It answers 404 when no features are stored. The response is the
shape parsed by ``services/brief/app/explanations.py``. It relates to ``main.py``.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from libs.common.internal_auth import require_service_token
from libs.domain.scoring import HOT_SCORE_THRESHOLD
from services.ml.app.explain import build_explanation
from services.ml.app.repository import MlRepository

BRIEF_SERVICE = "brief"
FEATURES = ("icp_fit", "signal_strength", "territory_opportunity")
BASELINE = 0.5
STEP = 0.1

router = APIRouter()


def explanation_from_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Build the payload for one stored score; weights are scaled to score points."""
    raw = {
        "icp_fit": float(inputs["weight_icp_fit"]),
        "signal_strength": float(inputs["weight_signal_strength"]),
        "territory_opportunity": float(inputs["weight_territory"]),
    }
    total = sum(raw.values())
    weights = {name: 100.0 * value / total for name, value in raw.items()}
    features = {name: float(inputs[f"feature_{name}"]) for name in FEATURES}
    payload = build_explanation(
        weights,
        features,
        {name: BASELINE for name in FEATURES},
        {name: (0.0, 1.0, STEP) for name in FEATURES},
        threshold=float(HOT_SCORE_THRESHOLD),
    )
    payload["company_id"] = str(inputs["company_id"])
    payload["score_version"] = inputs["score_version"]
    return payload


@router.get(
    "/internal/ml/explanations/{company_id}",
    dependencies=[Depends(require_service_token(BRIEF_SERVICE))],
)
async def get_explanation(company_id: UUID, request: Request) -> dict[str, Any]:
    repository: MlRepository = request.app.state.repository
    try:
        inputs = await repository.score_inputs(company_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if inputs is None:
        raise HTTPException(status_code=404, detail="explanation_not_found")
    return explanation_from_inputs(inputs)
