from __future__ import annotations

import math


_WILSON_Z_95 = 1.959963984540054


def wilson_interval(successes: int, trials: int, z: float = _WILSON_Z_95) -> dict[str, float] | None:
    """Return the two-sided Wilson score interval for a binomial rate.

    This is more informative than a raw percentage for small trade samples (for
    example, 19 wins from 20 trades). It describes sampling uncertainty; it is
    not a guarantee that future performance falls inside the interval.
    """
    successes = int(successes)
    trials = int(trials)
    if trials <= 0:
        return None
    if successes < 0 or successes > trials:
        raise ValueError("Success count must be between zero and the trial count")
    if not math.isfinite(z) or z <= 0:
        raise ValueError("z must be a positive finite number")

    observed = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    center = (observed + z_squared / (2.0 * trials)) / denominator
    margin = z * math.sqrt(
        observed * (1.0 - observed) / trials + z_squared / (4.0 * trials * trials)
    ) / denominator
    return {
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
    }
