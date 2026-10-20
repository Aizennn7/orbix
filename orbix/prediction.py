from __future__ import annotations

from dataclasses import dataclass, field

from .model import BootstrapEnsemble, OnlineLogisticModel


@dataclass(frozen=True)
class Prediction:
    """One model read for one completed candle.

    ``probability_up`` is the calibrated estimate. ``uncertainty`` is the
    standard deviation across bootstrap members; it measures how unstable the
    model is on this bar, not how likely a trade is to win.
    """

    probability_up: float
    raw_probability: float
    uncertainty: float | None
    ensemble_members: int
    calibrated: bool
    calibration_flat: bool = False
    contributions: list[dict] = field(default_factory=list)
    abstain_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "probability_up": self.probability_up,
            "raw_probability": self.raw_probability,
            "uncertainty": self.uncertainty,
            "ensemble_members": self.ensemble_members,
            "calibrated": self.calibrated,
            "calibration_flat": self.calibration_flat,
            "contributions": self.contributions,
            "abstain_reason": self.abstain_reason,
        }


def build_prediction(
    champion: OnlineLogisticModel,
    features: dict[str, float],
    *,
    ensemble: BootstrapEnsemble | None = None,
    max_uncertainty: float = 0.10,
) -> Prediction:
    # The champion supplies the probability. The bootstrap members are used
    # only to measure disagreement around it: the calibrator was fitted on
    # champion scores, so feeding it a different score would misstate the
    # mapping.
    raw = champion.predict_up(features)
    uncertainty: float | None = None
    members = 0
    if ensemble is not None and ensemble.ready:
        _, spread = ensemble.predict(features)
        uncertainty = spread
        members = len(ensemble.members)
    probability = champion.calibrator.transform(raw) if champion.calibrator.fitted else raw
    flat = champion.calibrator.flat
    reason = None
    if uncertainty is not None and uncertainty > max_uncertainty:
        reason = (
            f"Ensemble disagreement is {uncertainty:.3f}, wider than the {max_uncertainty:.2f} "
            "stability limit; abstaining instead of guessing."
        )
    return Prediction(
        probability_up=float(probability),
        raw_probability=float(raw),
        uncertainty=None if uncertainty is None else float(uncertainty),
        ensemble_members=members,
        calibrated=champion.calibrator.fitted,
        calibration_flat=flat,
        contributions=champion.contributions(features),
        abstain_reason=reason,
    )
