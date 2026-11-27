"""All registered ops, importable as plain functions for scripts::

from my_image_mcp import ops
img = ops.load_image("in.png").image
edges = ops.canny(ops.blur(img, ksize=5).image, auto=True).image
"""

from importlib import import_module

from ..core.registry import REGISTRY

MODULE_NAMES = [
    "io",
    "color",
    "filters",
    "threshold",
    "morphology",
    "contours",
    "geometry",
    "drawing",
    "composite",
    "features",
    "detection",
    "segmentation",
    "media",  # libvips (bundled wheel); tools skipped if unavailable
    "magick",  # ImageMagick 7 (optional system install); tools skipped if absent
]
for _m in MODULE_NAMES:
    import_module(f"{__name__}.{_m}")

# Expose every op function at package level (ops.blur, ops.threshold, ...).
globals().update({name: spec.func for name, spec in REGISTRY.items()})

__all__ = sorted(REGISTRY)
