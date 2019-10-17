from   chainer.utils import type_check
import math

from   chainer_compiler.elichika.typing import utils

unaryops = {
        '-'    : (6, lambda x: -x),
        'ceil' : (6, math.ceil),
        'abs'  : (6, abs),
        }

binops = {
        '+'  : (4, lambda x, y: x + y),
        '-'  : (4, lambda x, y: x - y),
        '*'  : (5, lambda x, y: x * y),
        '/'  : (5, lambda x, y: x / y),
        '//' : (5, lambda x, y: x // y),
        }

def _flip(func):
    return (lambda x, y: func(y, x))

def _make_unaryop(term, symbol):
    priority, func = unaryops[symbol]

    if term.value is None:
        return term
    expr = type_check.UnaryOperator(priority, term.expr, symbol, func)
    return ShapeElem(func(term.value), expr=expr)

def _make_binop(lhs, rhs, symbol):
    priority, func = binops[symbol]

    if not isinstance(rhs, ShapeElem):
        if lhs.value is None:
            return ShapeElem(None)
        expr = type_check.BinaryOperator(
                priority, lhs.expr, type_check.Constant(rhs), symbol, func)
        return ShapeElem(func(lhs.value, rhs), expr=expr)

    if not isinstance(lhs, ShapeElem):
        if rhs.value is None:
            return ShapeElem(None)
        expr = type_check.BinaryOperator(
                priority, type_check.Constant(lhs), rhs.expr, symbol, func)
        return ShapeElem(func(lhs, rhs.value), expr=expr)

    if lhs.value is None or rhs.value is None:
        return ShapeElem(None)
    expr = type_check.BinaryOperator(
            priority, lhs.expr, rhs.expr, symbol, func)
    return ShapeElem(func(lhs.value, rhs.value), expr=expr)


def _make_binop_expr(lhs_expr, rhs_expr, symbol):
    priority, func = binops[symbol]
    return type_check.BinaryOperator(
            priority, lhs_expr, rhs_expr, symbol, func)

def _try_eval(expr):
    try:
        return expr.eval()
    except Exception:
        return None


def simplify(expr):
    n = _try_eval(expr)
    if n is not None:
        return type_check.Constant(n)

    if isinstance(expr, type_check.BinaryOperator):
        if (expr.exp == '+' or expr.exp == '-') and _try_eval(expr.rhs) == 0:
            return simplify(expr.lhs)

        if expr.exp == '+' and _try_eval(expr.rhs) < 0:
            expr_rhs = type_check.Constant(- _try_eval(expr.rhs))
            return simplify(_make_binop_expr(expr.lhs, expr_rhs, '-'))

        if expr.exp == '-' and _try_eval(expr.rhs) < 0:
            expr_rhs = type_check.Constant(- _try_eval(expr.rhs))
            return simplify(_make_binop_expr(expr.lhs, expr_rhs, '+'))

        if (expr.exp == '*' or expr.exp == '/' or expr.exp == '//') and _try_eval(expr.rhs) == 1:
            return simplify(expr.lhs)

        if isinstance(expr.lhs, type_check.BinaryOperator) and \
                expr.lhs.priority == expr.priority:
            if expr.lhs.exp == '+' or expr.lhs.exp == '*':
                expr_exp = expr.exp
            elif expr.lhs.exp == '-':
                if expr.exp == '+':
                    expr_exp = '-'
                else:
                    expr_exp = '+'
            else:
                assert False

            _, expr_func = binops[expr_exp]
            expr_rhs = type_check.BinaryOperator(expr.priority,
                    expr.lhs.rhs, expr.rhs, expr_exp, expr_func)
            expr_rhs = simplify(expr_rhs)
            if isinstance(expr_rhs, type_check.Constant):
                return simplify(type_check.BinaryOperator(expr.lhs.priority,
                    expr.lhs.lhs, expr_rhs, expr.lhs.exp, expr.lhs.func))

        expr.lhs = simplify(expr.lhs)
        expr.rhs = simplify(expr.rhs)

    # if isinstance(expr, type_check.UnaryOperator):
    #     expr.term = simplify(expr.term)
    return expr


class ShapeElem():
    def __init__(self, value_or_name, expr=None):
        assert type(value_or_name) in [int, float, str, type(None)]
        if isinstance(value_or_name, str):
            # name
            self.value = None
            self.expr = type_check.Variable(None, value_or_name)
        else:
            # value
            self.value = value_or_name
            self.expr = simplify(expr) if expr is not None else type_check.Constant(value_or_name)

    def __str__(self):
        # self.expr = simplify(self.expr)
        if isinstance(self.expr, type_check.Constant):
            return str(self.value)
        return "{} ({})".format(self.value, self.expr)

    def __repr__(self):
        return self.__str__()

    def __neg__(self):
        return _make_unaryop(self, '-')
    def __ceil__(self):
        return _make_unaryop(self, 'ceil')
    def __abs__(self):
        return _make_unaryop(self, 'abs')

    def __add__(self, other):
        return _make_binop(self, other, '+')
    def __sub__(self, other):
        return _make_binop(self, other, '-')
    def __mul__(self, other):
        return _make_binop(self, other, '*')
    def __truediv__(self, other):
        return _make_binop(self, other, '/')
    def __floordiv__(self, other):
        return _make_binop(self, other, '//')

    def __gt__(self, other):
        if self.value is None or other.value is None:
            return True
        return self.value > other.value

    def __lt__(self, other):
        if self.value is None or other.value is None:
            return True
        return self.value < other.value


    __iadd__ = __add__
    __isub__ = __sub__
    __imul__ = __mul__
    __itruediv__ = __truediv__
    __ifloordiv__ = __floordiv__

    __radd__ = _flip(__add__)
    __rsub__ = _flip(__sub__)
    __rmul__ = _flip(__mul__)
    __rtruediv__ = _flip(__truediv__)
    __rfloordiv__ = _flip(__floordiv__)

    def __eq__(self, other):
        # XXX: equality against None should always be true
        if self.value is None:
            return True

        if isinstance(other, ShapeElem):
            if other.value is None:
                return True
            return self.value == other.value
        else:
            return self.value == other

    def has_value(self):
        return self.value is not None

    def get_value(self):
        return self.value


def wrap_shape(shape_seq): # Tuple[int or ShapeElem] -> Tuple[ShapeElem]
    return tuple([i if isinstance(i, ShapeElem) else ShapeElem(i) for i in shape_seq])

def unwrap_shape(shape_seq):
    return tuple([s.value if s.has_value() else 1 for s in shape_seq])

def is_incomplete_shape(shape_seq):
    return any([not s.has_value() for s in shape_seq])


def unify_shape(shape1, shape2):
    for e1, e2 in zip(shape1, shape2):
        if e1.value and e2.value:
            if e1.value != e2.value:
                e1.value = e2.value = None

        # TODO: which expr should we use?
        utils.set_attr_if_None(e1, e2, 'value')
