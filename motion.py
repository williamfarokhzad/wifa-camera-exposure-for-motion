"""Offline exposure budget for a planar, calibrated, translating scene.

No camera SDK, image analysis, strobe timing or hardware configuration.
"""
from dataclasses import dataclass, fields
import math


def number(value, name, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is too large") from exc
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return value


def finite(value):
    if not math.isfinite(value):
        raise ValueError("calculation overflow; use realistic units and magnitudes")
    return value


@dataclass(frozen=True)
class Exposure:
    speed_mm_s: float
    acceleration_bound_mm_s2: float
    scale_mm_per_px: float
    blur_limit_px: float
    exposure_us: float
    frame_rate_hz: float
    overhead_us: float
    device_min_us: float
    device_max_us: float
    device_step_us: float

    def __post_init__(self):
        for field in fields(self):
            value = number(getattr(self, field.name), field.name,
                           zero=field.name in {"speed_mm_s", "acceleration_bound_mm_s2", "overhead_us"})
            object.__setattr__(self, field.name, value)
        if self.device_min_us > self.device_max_us:
            raise ValueError("device_min_us must not exceed device_max_us")

    def displacement(self, seconds):
        seconds = number(seconds, "seconds", zero=True)
        return finite(self.speed_mm_s * seconds
                      + 0.5 * self.acceleration_bound_mm_s2 * seconds * seconds)

    def motion_limit_us(self):
        """Stable positive root of a*t^2/2 + v*t = blur_limit*scale.

        None means no motion-imposed bound in this model, not infinite
        exposure on a real camera.
        """
        distance = finite(self.blur_limit_px * self.scale_mm_per_px)
        if distance == 0:
            raise ValueError("blur distance underflow; use realistic units")
        v, a = self.speed_mm_s, self.acceleration_bound_mm_s2
        if a == 0:
            return None if v == 0 else finite(distance / v * 1e6)
        root = math.sqrt(finite(v * v + 2 * a * distance))
        seconds = finite(2 * distance / finite(v + root))
        return finite(seconds * 1e6)

    def on_device_grid(self, exposure):
        if not self.device_min_us <= exposure <= self.device_max_us:
            return False
        steps = finite((exposure - self.device_min_us) / self.device_step_us)
        return math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-9)

    def result(self):
        motion = self.motion_limit_us()
        period = finite(1e6 / self.frame_rate_hz)
        budget = finite(period - self.overhead_us)
        upper = min(self.device_max_us, budget, motion if motion is not None else math.inf)
        recommendation = None
        if upper >= self.device_min_us:
            steps = math.floor(finite((upper - self.device_min_us) / self.device_step_us))
            recommendation = finite(self.device_min_us + steps * self.device_step_us)
            # A floating-point multiply must never round above the upper bound.
            if recommendation > upper:
                recommendation -= self.device_step_us
            if recommendation < self.device_min_us:
                recommendation = None
        blur = finite(self.displacement(self.exposure_us / 1e6) / self.scale_mm_per_px)
        checks = {
            "motion_budget_ok": blur <= self.blur_limit_px,
            "nonoverlap_frame_budget_ok": self.exposure_us <= budget,
            "device_grid_ok": self.on_device_grid(self.exposure_us),
        }
        return {
            "predicted_blur_bound_px": blur,
            "motion_limit_us": motion,
            "nonoverlap_frame_budget_us": budget,
            "upper_bound_us": upper,
            "recommended_grid_exposure_us": recommendation,
            "feasible_in_model": recommendation is not None,
            "requested_exposure_ok_in_model": all(checks.values()),
            "checks": checks,
        }


def run_document(document):
    expected = {field.name for field in fields(Exposure)}
    if not isinstance(document, dict) or set(document) != expected:
        raise ValueError("JSON must contain exactly these fields: " + ", ".join(sorted(expected)))
    return Exposure(**document).result()
