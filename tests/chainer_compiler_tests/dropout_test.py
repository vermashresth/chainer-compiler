import os
import sys

import chainerx
import chainerx.testing
import numpy as np


project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(project_root, 'build/chainer_compiler_cc'))
sys.path.append(os.path.join(project_root, 'chainer_compiler'))

import _chainer_compiler_core

ONNX_TEST_DATA = 'third_party/onnx/onnx/backend/test/data'


def test_dropout_inference():
    graph = _chainer_compiler_core.load(
        os.path.join(ONNX_TEST_DATA, 'node/test_dropout_random/model.onnx'))
    input_names = graph.input_names()
    output_names = graph.output_names()
    assert len(input_names) == 1
    assert len(output_names) == 1

    chxvm = graph.compile()
    input = chainerx.array(np.random.normal(size=(3, 4, 5)).astype(np.float32))
    inputs = {input_names[0]: _chainer_compiler_core.value(input)}
    outputs = chxvm.run(inputs)
    output = outputs[output_names[0]].array()

    assert bool(chainerx.sum(input != output) == 0)


def test_dropout_training():
    graph = _chainer_compiler_core.load(
        os.path.join(ONNX_TEST_DATA, 'node/test_dropout_random/model.onnx'))
    input_names = graph.input_names()
    output_names = graph.output_names()
    assert len(input_names) == 1
    assert len(output_names) == 1

    chxvm = graph.compile()
    input = chainerx.array(np.random.normal(size=(3, 4, 5)).astype(np.float32))
    inputs = {input_names[0]: _chainer_compiler_core.value(input)}

    num_retries = 3
    for i in range(num_retries):
        outputs = chxvm.run(inputs, training=True)
        output = outputs[output_names[0]].array()
        ok = bool(chainerx.sum(input != output) > 0)
        if ok: break
    else:
        assert False, 'No dropout was observed in %d attempts' % num_retries


# TODO(hamaji): Implement and test the backprop of Dropout.
