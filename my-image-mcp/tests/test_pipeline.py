from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest

from my_image_mcp.config import Settings
from my_image_mcp.core.engine import Engine


def _build(engine, img):
    blurred = engine.execute("blur", {"image": img, "ksize": 7})["outputs"][0]["handle"]
    mask = engine.execute("threshold", {"image": blurred, "method": "otsu"})["outputs"][0]["handle"]
    cnt = engine.execute("find_contours", {"image": mask, "min_area": 50})["outputs"][0]["handle"]
    drawn = engine.execute("draw_contours", {"image": img, "contours": cnt, "color": "magenta"})["outputs"][0]["handle"]
    clahe = engine.execute("cv_call", {"function": "createCLAHE", "kwargs": {"clipLimit": 3.0}})
    obj = clahe["result"]["handle"]
    gray = engine.execute("cv_call", {"function": "cvtColor", "args": [drawn, "cv2.COLOR_BGR2GRAY"]})
    final = engine.execute("cv_call", {"function": f"{obj}.apply", "args": [gray["result"]["handle"]]})
    return final["result"]["handle"]


def test_json_export_replays_identically(engine, img, root):
    final = _build(engine, img)
    exported = engine.export_json([final])
    assert [s["op"] for s in exported["steps"]][0] == "load_image"

    fresh = Engine(Settings(roots=[root]))
    res = fresh.run_pipeline(json.loads(json.dumps(exported))["steps"])
    assert res["ok"], res
    replayed = fresh.store.get(res["aliases"][final])
    assert np.array_equal(replayed, engine.store.get(final))


def test_python_export_runs(engine, img, root, tmp_path):
    final = _build(engine, img)
    script = engine.export_python([final])
    script += f"\nnp.save({str(tmp_path / 'out.npy')!r}, {final})\n"
    path = tmp_path / "pipeline.py"
    path.write_text(script)
    subprocess.run([sys.executable, str(path)], check=True, cwd=root)
    assert np.array_equal(np.load(tmp_path / "out.npy"), engine.store.get(final))


def test_pipeline_aliases_and_prev(engine, img):
    res = engine.run_pipeline(
        [
            {"op": "convert_color", "args": {"image": img, "to": "gray"}, "outputs": ["g"]},
            {"op": "blur", "args": {"image": "$prev", "ksize": 3}},
            {"op": "difference", "args": {"image1": "g", "image2": "$prev"}},
        ]
    )
    assert res["ok"] and len(res["results"]) == 3


def test_pipeline_reports_failing_step(engine, img):
    res = engine.run_pipeline(
        [
            {"op": "blur", "args": {"image": img}},
            {"op": "nope", "args": {}},
        ]
    )
    assert not res["ok"] and res["failed_step"] == 1 and len(res["results"]) == 1


def test_cv_call_blocks_file_and_gui_access(engine):
    for fn in ("imread", "imwrite", "imshow", "dnn.readNetFromONNX", "os.system", "utils.dumpInputArray"):
        with pytest.raises(Exception, match="not available|not an OpenCV|does not exist|Invalid"):
            engine.execute("cv_call", {"function": fn, "args": []})


def test_cv_call_constants_and_tuple_results(engine, img):
    gray = engine.execute("cv_call", {"function": "cvtColor", "args": [img, "cv2.COLOR_BGR2GRAY"]})["result"]["handle"]
    res = engine.execute(
        "cv_call", {"function": "threshold", "args": [gray, 0, 255, "cv2.THRESH_BINARY | cv2.THRESH_OTSU"]}
    )["result"]
    assert isinstance(res[0], float) and res[1]["kind"] == "image"
    m = engine.execute("cv_call", {"function": "getRotationMatrix2D", "args": [[10, 10], 45, 1.0]})["result"]
    assert np.array(m).shape == (2, 3)


def test_redraw_on_same_input_warns(engine, img):
    first = engine.execute("draw_circle", {"image": img, "cx": 50, "cy": 50, "radius": 10})
    assert "warning" not in first
    drawn = first["outputs"][0]["handle"]
    # Drawing on the original again (e.g. a parallel call) drops the circle: warn.
    second = engine.execute("draw_ellipse", {"image": img, "cx": 90, "cy": 90, "axis_x": 20, "axis_y": 10})
    assert drawn in second["warning"] and "draw_circle" in second["warning"]
    # Chaining on the drawn output is the intended pattern: no warning.
    third = engine.execute("draw_line", {"image": drawn, "x1": 0, "y1": 0, "x2": 10, "y2": 10})
    assert "warning" not in third
    # Non-drawing ops on an already-drawn input are fine.
    assert "warning" not in engine.execute("blur", {"image": img, "ksize": 3})


def test_drawing_descriptions_explain_chaining():
    from my_image_mcp.core.registry import REGISTRY

    drawing = [s for s in REGISTRY.values() if s.module == "drawing"]
    assert drawing and all("NEW image handle" in s.description for s in drawing)
