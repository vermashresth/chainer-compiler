# coding: utf-8

import chainer
import chainer.links as L

# Network definition


class A(chainer.Chain):

    def __init__(self, n_vocab, n_out):
        super(A, self).__init__()
        with self.init_scope():
            self.l1 = L.EmbedID(n_vocab, n_out)

    def forward(self, x):
        return self.l1(x)

# ======================================


from chainer_compiler import ch2o

if __name__ == '__main__':
    import numpy as np
    np.random.seed(314)

    n_vocab = 7
    n_out = 3
    n_batch = 5

    model = A(n_vocab, n_out)

    v = np.random.randint(n_vocab, size=n_batch)
    ch2o.generate_testcase(model, [v], backprop=True)
