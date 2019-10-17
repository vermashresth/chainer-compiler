# coding: utf-8

import chainer
import chainer.links as L
from chainer_compiler.elichika import testtools

# Network definition


class A(chainer.Chain):

    def __init__(self, n_layer, n_in, n_out):
        super(A, self).__init__()
        with self.init_scope():
            self.l1 = L.NStepLSTM(n_layer, n_in, n_out, 0.1)

    def forward(self, x):
        hy, cs, ys = self.l1(None, None, x)
        return hy, cs, ys
        # return hy,cs


# ======================================
def main():
    import numpy as np
    np.random.seed(314)

    n_batch = 7
    n_layer = 3
    n_in = 8
    n_hidden = 5
    n_maxlen = 10

    # n_batch = 2
    # n_layer = 2
    # n_in = 2
    # n_hidden = 4

    model = A(n_layer, n_in, n_hidden)

    # ilens = np.random.randint(1,n_maxlen,size=n_batch)
    ilens = [t for t in range(n_batch)]
    xs = [np.random.rand(i+4, n_in).astype(np.float32) for i in ilens]
    testtools.generate_testcase(model, [xs])

if __name__ == '__main__':
    main()