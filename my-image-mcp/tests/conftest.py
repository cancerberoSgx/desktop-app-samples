from __future__ import annotations

import cv2
import numpy as np
import pytest

from my_image_mcp.config import Settings
from my_image_mcp.core.engine import Engine


def make_shapes(w: int = 320, h: int = 240) -> np.ndarray:
    """Dark background with a white circle, a red rectangle and a green triangle."""
    img = np.full((h, w, 3), 30, np.uint8)
    cv2.circle(img, (70, 70), 40, (255, 255, 255), -1)
    cv2.rectangle(img, (160, 40), (280, 110), (0, 0, 255), -1)
    cv2.fillPoly(img, [np.array([[60, 220], [140, 220], [100, 150]], np.int32)], (0, 255, 0))
    return img


@pytest.fixture
def root(tmp_path):
    cv2.imwrite(str(tmp_path / "shapes.png"), make_shapes())
    return tmp_path


@pytest.fixture
def engine(root) -> Engine:
    return Engine(Settings(roots=[root]))


@pytest.fixture
def img(engine) -> str:
    return engine.execute("load_image", {"path": "shapes.png"})["outputs"][0]["handle"]
