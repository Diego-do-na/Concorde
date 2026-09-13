#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
import onnx
from onnx import helper, TensorProto, numpy_helper

HERE = Path(__file__).parent
OUT = HERE / "dummy_fc1.onnx"
META = HERE / "dummy_fc1.onnx.meta.json"

# read canonical feature names
contract = Path(__file__).resolve().parents[3] / "artifacts" / "feature_contract_fc-1.json"
with open(contract, "r") as f:
    fc = json.load(f)
feature_names = fc["features"]

# build a tiny model: input (1,23) -> MatMul with W(23,1) -> Add bias (1) -> Sigmoid -> output (1,1)
input_tensor = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 23])
output_tensor = helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", 1])

W = np.ones((23,1), dtype=np.float32)
B = np.zeros((1,), dtype=np.float32)
W_init = numpy_helper.from_array(W, name="W")
B_init = numpy_helper.from_array(B, name="B")

matmul_node = helper.make_node("MatMul", ["input", "W"], ["matmul_out"], name="matmul")
add_node = helper.make_node("Add", ["matmul_out", "B"], ["add_out"], name="add")
sig_node = helper.make_node("Sigmoid", ["add_out"], ["output"], name="sigmoid")

graph = helper.make_graph(
    [matmul_node, add_node, sig_node],
    "dummy_fc1",
    [input_tensor],
    [output_tensor],
    initializer=[W_init, B_init],
)

model = helper.make_model(graph, producer_name="concorde-dummy")
onnx.save(model, str(OUT))
print("wrote", OUT)

# meta.json
meta = {
    "model_version": "concorde-dummy-0",
    "feature_contract": fc["contract"],
    "calibration": {"type": "platt", "a": 1.0, "b": 0.0},
    "threshold": 0.5,
    "git_sha": None,
    "seed": 42,
    "manifest_sha256": None,
    "feature_names": feature_names,
    "train_feature_means": [0.0]*23,
    "train_feature_stds": [1.0]*23,
    "feature_importance": [0.0]*23,
    "direction_sign": [1]*23
}

with open(META, "w") as f:
    json.dump(meta, f, indent=2)
print("wrote", META)

