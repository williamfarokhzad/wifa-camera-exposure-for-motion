import json
import math
from pathlib import Path
import random
import subprocess
import sys
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext

from motion import Exposure, run_document

ROOT = Path(__file__).resolve().parents[1]


def example(name):
    return json.loads((ROOT / "examples" / f"{name}.json").read_text())


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.base = Exposure(**example("conveyor"))

    def test_hand_calculated_conveyor(self):
        r = self.base.result()
        self.assertAlmostEqual(r["predicted_blur_bound_px"], 3)
        self.assertAlmostEqual(r["motion_limit_us"], 1000 / 3)
        self.assertEqual(r["recommended_grid_exposure_us"], 333)
        self.assertFalse(r["checks"]["motion_budget_ok"])
        self.assertTrue(r["checks"]["nonoverlap_frame_budget_ok"])

    def test_acceleration_from_rest(self):
        r = run_document(example("accelerating"))
        self.assertAlmostEqual(r["predicted_blur_bound_px"], 1.6)
        self.assertAlmostEqual(r["motion_limit_us"], math.sqrt(0.1 / 10000) * 1e6)
        self.assertEqual(r["recommended_grid_exposure_us"], 3160)

    def test_device_infeasible_is_not_clamped_to_a_false_pass(self):
        r = run_document(example("device-limit"))
        self.assertAlmostEqual(r["motion_limit_us"], 50)
        self.assertAlmostEqual(r["predicted_blur_bound_px"], 1.2)
        self.assertIsNone(r["recommended_grid_exposure_us"])
        self.assertFalse(r["feasible_in_model"])

    def test_stationary_has_no_motion_bound_but_has_frame_limit(self):
        r = replace(self.base, speed_mm_s=0, frame_rate_hz=200, overhead_us=500).result()
        self.assertIsNone(r["motion_limit_us"])
        self.assertEqual(r["recommended_grid_exposure_us"], 4500)
        self.assertEqual(r["predicted_blur_bound_px"], 0)

    def test_empty_frame_budget(self):
        r = replace(self.base, overhead_us=20000).result()
        self.assertLess(r["nonoverlap_frame_budget_us"], 0)
        self.assertFalse(r["feasible_in_model"])
        self.assertFalse(r["checks"]["nonoverlap_frame_budget_ok"])

    def test_grid_is_anchored_at_device_minimum(self):
        e = replace(self.base, device_min_us=7, device_step_us=10, exposure_us=327)
        self.assertEqual(e.result()["recommended_grid_exposure_us"], 327)
        self.assertTrue(e.result()["requested_exposure_ok_in_model"])
        self.assertFalse(replace(e, exposure_us=330).result()["checks"]["device_grid_ok"])

    def test_linear_time_and_speed_sensitivity(self):
        b = self.base.result()["predicted_blur_bound_px"]
        self.assertAlmostEqual(replace(self.base, speed_mm_s=600).result()["predicted_blur_bound_px"], 2*b)
        self.assertAlmostEqual(replace(self.base, exposure_us=500).result()["predicted_blur_bound_px"], b/2)

    def test_independent_decimal_distance_and_bisection_reference(self):
        # Independent root search, not another copy of the quadratic formula.
        rng = random.Random(339)
        with localcontext() as ctx:
            ctx.prec = 50
            for _ in range(300):
                v, a = rng.randint(0, 3000), rng.randint(1, 50000)
                scale = Decimal(rng.randint(1, 100)) / 1000
                pixels = Decimal(rng.randint(1, 5))
                distance = scale * pixels
                lo, hi = Decimal(0), Decimal(10)
                for _ in range(180):
                    mid = (lo + hi) / 2
                    traveled = Decimal(v)*mid + Decimal(a)*mid*mid/2
                    if traveled <= distance:
                        lo = mid
                    else:
                        hi = mid
                e = replace(self.base, speed_mm_s=v, acceleration_bound_mm_s2=a,
                            scale_mm_per_px=float(scale), blur_limit_px=float(pixels),
                            device_min_us=0.001, device_step_us=0.001)
                self.assertAlmostEqual(e.motion_limit_us(), float(lo * 1000000), delta=1e-8)
                r = e.result()
                t = Decimal(str(r["recommended_grid_exposure_us"])) / 1000000
                self.assertLessEqual(Decimal(v)*t + Decimal(a)*t*t/2, distance)
                self.assertTrue(e.on_device_grid(r["recommended_grid_exposure_us"]))

    def test_strict_finite_numbers(self):
        for value in [True, "300", None, float("nan"), float("inf"), -1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.base, speed_mm_s=value)
        for field in ["scale_mm_per_px", "blur_limit_px", "exposure_us", "frame_rate_hz", "device_step_us"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.base, **{field: 0})

    def test_invalid_device_range_and_overflow(self):
        with self.assertRaises(ValueError):
            replace(self.base, device_min_us=100, device_max_us=10)
        with self.assertRaises(ValueError):
            replace(self.base, acceleration_bound_mm_s2=1e308, speed_mm_s=1e308).result()
        with self.assertRaises(ValueError):
            replace(self.base, scale_mm_per_px=1e-300, blur_limit_px=1e-300).result()

    def test_document_has_no_silent_typo_defaults(self):
        for document in [[], {}, {**example("conveyor"), "fps": 60}]:
            with self.subTest(document=document), self.assertRaises(ValueError):
                run_document(document)

    def test_all_example_cli_outputs(self):
        for name in ["conveyor", "accelerating", "device-limit"]:
            output = subprocess.run([sys.executable, "main.py", f"examples/{name}.json"],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(json.loads(output.stdout), run_document(example(name)))

    def test_cli_error_has_no_success_json_or_traceback(self):
        p = subprocess.run([sys.executable, "main.py", "missing-example.json"], cwd=ROOT,
                           text=True, capture_output=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, "")
        self.assertIn("error:", p.stderr)
        self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()
