# coding: utf-8

import chainer
import chainer.functions as F
from chainer_compiler.elichika import testtools

class MaxPool(chainer.Chain):
    def forward(self, x):
        y1 = F.max_pooling_2d(x, (1, 3), stride=(1, 4))
        return y1


class MaxPoolPad(chainer.Chain):
    def forward(self, x):
        y1 = F.max_pooling_2d(x, (1, 3), stride=(1, 4), pad=(0, 1))
        return y1


class MaxPoolNoStride(chainer.Chain):
    def forward(self, x):
        y1 = F.max_pooling_2d(x, (3, 4))
        return y1


# ======================================

from chainer_compiler.elichika import testtools
import numpy as np

def main():
    x = np.random.rand(2, 3, 19, 13).astype(np.float32)
    testtools.generate_testcase(MaxPool, [x])

    testtools.generate_testcase(MaxPoolPad, [x], subname='pad')

    testtools.generate_testcase(MaxPoolNoStride, [x], subname='no_stride')


if __name__ == '__main__':
    main()