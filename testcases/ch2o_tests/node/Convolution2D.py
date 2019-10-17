# coding: utf-8

import chainer
import chainer.links as L


class A(chainer.Chain):

    def __init__(self):
        super(A, self).__init__()
        with self.init_scope():
            # TODO(satos) テストケース増やす
            self.l1 = L.Convolution2D(None, 6, (5, 7), stride=(2, 3))

    def forward(self, x):
        y1 = self.l1(x)
        return y1


# ======================================

from chainer_compiler import ch2o
import numpy as np

if __name__ == '__main__':

    model = A()

    np.random.seed(123)
    x = v = np.random.rand(2, 20, 15, 17).astype(np.float32)
    ch2o.generate_testcase(model, [x])
