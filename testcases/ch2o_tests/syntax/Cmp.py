# coding: utf-8

import chainer
import chainer.functions as F


class Equal(chainer.Chain):
    def forward(self, x, y):
        return x == y


class NotEqual(chainer.Chain):
    def forward(self, x, y):
        return x != y


class Is(chainer.Chain):
    def forward(self, x, y):
        return x is y


class IsNot(chainer.Chain):
    def forward(self, x, y):
        return x is not y


class GreaterThan(chainer.Chain):
    def forward(self, x, y):
        return x > y


class GreaterEqual(chainer.Chain):
    def forward(self, x, y):
        return x >= y


class LessThan(chainer.Chain):
    def forward(self, x, y):
        return x < y


class LessEqual(chainer.Chain):
    def forward(self, x, y):
        return x <= y


# ======================================


from chainer_compiler import ch2o
import numpy as np


if __name__ == '__main__':
    for name, cls in [('eq', Equal),
                      ('neq', NotEqual),
                      ('gt', GreaterThan),
                      ('ge', GreaterEqual),
                      ('lt', LessThan),
                      ('le', LessEqual)]:
        for x, y in [(4, 5), (4, 4), (4, 3)]:
            ch2o.generate_testcase(cls(), [x, y],
                                   subname='%d_%s_%d' % (x, name, y))

    for name, cls in [('is', Is),
                      ('isnt', IsNot)]:
        for x, y in [(None, None),
                     (42, None),
                     (True, None),
                     (True, False),
                     (True, True),
                     (False, False),
                     (True, [42]),
                     ([42], [42]),
                     (np.array(42), np.array(42))]:
            ch2o.generate_testcase(cls(), [x, y],
                                   subname='%s_%s_%s' % (x, name, y))

