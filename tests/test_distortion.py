import numpy as np
from conftest import H, W, make_wcs

from astro_search import distortion, projection


def sample(w, rng, n=400, noise_px=0.0):
    x, y = rng.uniform(0, W, n), rng.uniform(0, H, n)
    ra, dec = w.all_pix2world(x + rng.normal(0, noise_px, n), y + rng.normal(0, noise_px, n), 0)
    return x, y, ra, dec


def test_radial_model_recovers_wavy_distortion_exactly(rng):
    truth = make_wcs({2: 3e-10, 4: -1e-16})
    x, y, ra, dec = sample(truth, rng)
    f = distortion.fit(x, y, ra, dec, W, H, 80.1, 19.9, (3, (4, 6)))
    assert distortion.residuals_px(f, x, y, ra, dec).max() < 1e-3
    # Експортований TAN-SIP дає те саме, що й сама підгонка
    w = f.to_wcs()
    gx, gy = np.meshgrid(np.linspace(0, W - 1, 9), np.linspace(0, H - 1, 7))
    r1, d1 = w.all_pix2world(gx.ravel(), gy.ravel(), 0)
    r2, d2 = truth.all_pix2world(gx.ravel(), gy.ravel(), 0)
    sep = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", projection.unit_vectors(r1, d1),
                                                  projection.unit_vectors(r2, d2)), -1, 1)))
    assert sep.max() / 0.02 < 1e-3


def test_cubic_cannot_follow_wavy_distortion(rng):
    truth = make_wcs({2: 3e-10, 4: -1e-16})
    x, y, ra, dec = sample(truth, rng)
    f = distortion.fit(x, y, ra, dec, W, H, 80, 20, (3, ()))
    assert distortion.residuals_px(f, x, y, ra, dec).max() > 0.5


def test_choose_prefers_plain_tan_without_distortion(rng):
    x, y, ra, dec = sample(make_wcs(), rng, noise_px=0.3)
    model, scores = distortion.choose(x, y, ra, dec, W, H, 80, 20)
    assert model == (1, ())
    assert set(scores) == set(distortion.MODELS)


def test_choose_takes_radial_terms_for_wavy_distortion(rng):
    x, y, ra, dec = sample(make_wcs({2: 3e-10, 4: -1e-16}), rng, noise_px=0.3)
    model, _ = distortion.choose(x, y, ra, dec, W, H, 80, 20)
    assert model[1], model


def test_few_stars_limit_the_models(rng):
    assert distortion.feasible(20) == [(1, ())]
    assert (2, ()) in distortion.feasible(40)
    assert (5, ()) not in distortion.feasible(60)


def test_describe_model():
    assert distortion.describe_model((1, ())) == "без дисторсії (TAN)"
    assert distortion.describe_model((3, (4, 6))) == "поліном 3-го степеня + радіальні r⁴, r⁶"
