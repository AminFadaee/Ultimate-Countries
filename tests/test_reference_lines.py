import numpy as np
from shapely.geometry import Point

from geography.reference_lines import reading_angle
from geography.render import ring_groups


def test_rings_merge_only_when_they_would_overlap():
    far = [np.array([[0.0, 0.0]]), np.array([[5.0, 0.0]])]
    near = [np.array([[0.0, 0.0]]), np.array([[0.1, 0.0]])]
    assert len(ring_groups(far, 0.12)) == 2
    assert len(ring_groups(near, 0.12)) == 1


def test_every_island_point_lies_inside_its_ring():
    parts = [np.array([[x, y], [x + 0.05, y]]) for x, y in [(0, 0), (0.2, 0.1), (3, 3), (3.1, 3.3), (8, 1)]]
    rings = ring_groups(parts, 0.12)
    for point in np.vstack(parts):
        assert any(np.hypot(*(point - [ring.x, ring.y])) <= ring.radius for ring in rings)


def test_labels_read_left_to_right_or_upwards():
    assert reading_angle(Point(0, 0), Point(-1, 0)) == 0
    assert reading_angle(Point(0, 1), Point(0, 0)) == 90
    assert reading_angle(Point(0, 0), Point(0, 1)) == 90
