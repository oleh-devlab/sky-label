import numpy as np
from conftest import H, W, make_wcs

from astro_search import projection


def test_world2pix_roundtrip_inside_frame(rng):
    w = make_wcs({2: -2e-9})
    x, y = rng.uniform(0, W, 200), rng.uniform(0, H, 200)
    ra, dec = w.all_pix2world(x, y, 0)
    px, py = projection.world2pix(w, ra, dec)
    assert np.nanmax(np.hypot(px - x, py - y)) < 0.01


def test_world2pix_marks_divergent_points_instead_of_raising():
    # Сильна дисторсія: astropy.all_world2pix на такому масиві кидає NoConvergence
    w = make_wcs({2: 1e-7})
    ra = 80 + np.linspace(-60, 60, 400)
    dec = np.clip(20 + np.linspace(-50, 50, 400), -89, 89)
    near = projection.near_field(w, W, H, ra, dec)
    px, py = projection.world2pix(w, ra[near], dec[near])
    ok = np.isfinite(px)
    assert ok.any() and not ok.all()
    # Прийняті точки повертаються прямим перетворенням не далі ніж за 0,5 px
    r2, d2 = w.all_pix2world(px[ok], py[ok], 0)
    cos = np.einsum("ij,ij->i", projection.unit_vectors(r2, d2), projection.unit_vectors(ra[near][ok], dec[near][ok]))
    assert (np.degrees(np.arccos(np.clip(cos, -1, 1))) / projection.pixel_scale_deg(w)).max() < 0.5


def test_near_field_rejects_the_other_side_of_the_sky():
    w = make_wcs()
    mask = projection.near_field(w, W, H, np.array([80.0, 260.0]), np.array([20.0, -20.0]))
    assert mask.tolist() == [True, False]


def test_stars_in_frame_returns_only_points_inside():
    w = make_wcs()
    ra, dec = w.all_pix2world([10.0, W / 2, W + 50.0], [10.0, H / 2, 10.0], 0)
    idx, x, y = projection.stars_in_frame(w, W, H, ra, dec)
    assert idx.tolist() == [0, 1]
    assert np.allclose(x, [10, W / 2]) and np.allclose(y, [10, H / 2])


def test_frame_center_is_crval_for_centered_wcs():
    ra, dec = projection.frame_center(make_wcs(crval=(359.9, 89.5)), W, H)
    assert abs(ra - 359.9) < 1e-9 and abs(dec - 89.5) < 1e-9


def test_clip_polyline_splits_at_frame_edges():
    x = np.array([10, 20, -5, -6, 30, 40, np.nan, 50])
    y = np.array([10, 10, 10, 10, 10, 10, 10, 10])
    assert projection.clip_polyline(x, y, W, H) == [[[10.0, 10.0], [20.0, 10.0]], [[30.0, 10.0], [40.0, 10.0]]]


def test_wcs_from_header_roundtrip():
    w = make_wcs({2: -2e-9})
    w2 = projection.wcs_from_header(dict(w.to_header(relax=True)))
    assert np.allclose(w2.all_pix2world([[100, 200]], 0), w.all_pix2world([[100, 200]], 0))
