import unittest
import ast, gast
from chainer_compiler.elichika.parser import canonicalizer
from chainer_compiler.elichika.parser import utils
from chainer_compiler.elichika.testtools import compare_ast, assert_semantically_equals


class Return(unittest.TestCase):
    def setUp(self):
        self.canonicalizer = canonicalizer.Canonicalizer(use_illegal_identifier=False)

    def test_return_simple(self):
        orig_code = utils.clip_head("""
        def func(a, b):
            return a + b

        value = func(3, 39)
        """)
        target_code = utils.clip_head("""
        def func(a, b):
            returned_value = None
            returned_1 = False

            returned_1 = True
            returned_value = a + b
            
            return returned_value

        value = func(3, 39)
        """)
        orig_ast = gast.ast_to_gast(ast.parse(orig_code))
        target_ast = gast.ast_to_gast(ast.parse(target_code))
        converted_ast = self.canonicalizer.visit(orig_ast)

        assert_semantically_equals(orig_code, target_code, ['value'])
        assert compare_ast(converted_ast, target_ast)
        assert compare_ast(target_ast, converted_ast)

    def test_return(self):
        orig_code = utils.clip_head("""
        def func(a, b):
            for i in range(a):
                if i == b:
                    return i
            return 0

        value = 0
        for a in range(10):
            for b in range(10):
                value += func(a, b)
        """)
        target_code = utils.clip_head("""
        def func(a, b):
            returned_value = None
            returned_1 = False
            for i in range(a):
                if i == b:
                    returned_1 = True
                    returned_value = i
                keepgoing = not returned_1
                if returned_1:
                    break
            if not returned_1:
                returned_1 = True
                returned_value = 0
            return returned_value

        value = 0
        for a in range(10):
            for b in range(10):
                value += func(a, b)
        """)
        orig_ast = gast.ast_to_gast(ast.parse(orig_code))
        target_ast = gast.ast_to_gast(ast.parse(target_code))
        converted_ast = self.canonicalizer.visit(orig_ast)

        assert_semantically_equals(orig_code, target_code, ['value'])
        assert compare_ast(converted_ast, target_ast)
        assert compare_ast(target_ast, converted_ast)

    def test_return_continue(self):
        orig_code = utils.clip_head("""
        def func(a, b, c):
            x = 0
            for i in range(a):
                if i == b:
                    continue
                for j in range(a):
                    if j == c:
                        continue
                    if j == b:
                        return x
                    x += i * j
            return x

        value = 0
        for a in range(10):
            for b in range(10):
                for c in range(10):
                    value += func(a, b, c)
        """)
        target_code = utils.clip_head("""
        def func(a, b, c):
            returned_value = None
            returned_1 = False
            x = 0
            for i in range(a):
                continued_1 = False
                if i == b:
                    continued_1 = True
                if not continued_1:
                    for j in range(a):
                        continued_2 = False
                        if j == c:
                            continued_2 = True
                        if not continued_2:
                            if j == b:
                                if not continued_2:
                                    returned_1 = True
                                    returned_value = x
                        if not continued_2 and not returned_1:
                            x += i * j
                        keepgoing = not returned_1
                        if returned_1:
                            break
                keepgoing = not returned_1
                if returned_1:
                    break
            if not returned_1:
                returned_1 = True
                returned_value = x
            return returned_value

        value = 0
        for a in range(10):
            for b in range(10):
                for c in range(10):
                    value += func(a, b, c)
        """)
        orig_ast = gast.ast_to_gast(ast.parse(orig_code))
        target_ast = gast.ast_to_gast(ast.parse(target_code))
        converted_ast = self.canonicalizer.visit(orig_ast)

        assert_semantically_equals(orig_code, target_code, ['value'])
        assert compare_ast(converted_ast, target_ast)
        assert compare_ast(target_ast, converted_ast)


    def test_return_break_continue(self):
        orig_code = utils.clip_head("""
        def func(a, b, c):
            x = 0
            for i in range(a):
                if i == b:
                    continue
                for j in range(a):
                    if j == c:
                        break
                    if j == b:
                        return x
                    x += i * j
            return x

        value = 0
        for a in range(10):
            for b in range(10):
                for c in range(10):
                    value += func(a, b, c)
        """)
        target_code = utils.clip_head("""
        def func(a, b, c):
            returned_value = None
            returned_1 = False
            x = 0
            for i in range(a):
                continued_1 = False
                if i == b:
                    continued_1 = True
                if not continued_1:
                    for j in range(a):
                        breaked_2 = False
                        if j == c:
                            breaked_2 = True
                        if not breaked_2:
                            if j == b:
                                if not breaked_2:
                                    returned_1 = True
                                    returned_value = x
                        if not breaked_2 and not returned_1:
                            x += i * j
                        keepgoing = not (breaked_2 or returned_1)
                        if breaked_2 or returned_1:
                            break
                keepgoing = not returned_1
                if returned_1:
                    break
            if not returned_1:
                returned_1 = True
                returned_value = x
            return returned_value

        value = 0
        for a in range(10):
            for b in range(10):
                for c in range(10):
                    value += func(a, b, c)
        """)
        orig_ast = gast.ast_to_gast(ast.parse(orig_code))
        target_ast = gast.ast_to_gast(ast.parse(target_code))
        converted_ast = self.canonicalizer.visit(orig_ast)

        assert_semantically_equals(orig_code, target_code, ['value'])
        assert compare_ast(converted_ast, target_ast)
        assert compare_ast(target_ast, converted_ast)

    def test_return_nested(self):
        orig_code = utils.clip_head("""
        def func(a, b):
            def func1(a, b):
                for i in range(a):
                    if i == b:
                        return i + 1
                return 0
            for i in range(func1(a, b)):
                if i == b:
                    return i
            return 0

        value = 0
        for a in range(10):
            for b in range(10):
                value += func(a, b)
        """)
        target_code = utils.clip_head("""
        def func(a, b):
            returned_value = None
            returned_1 = False

            def func1(a, b):
                returned_value = None
                returned_2 = False
                for i in range(a):
                    if i == b:
                        returned_2 = True
                        returned_value = i + 1
                    keepgoing = not returned_2
                    if returned_2:
                        break
                if not returned_2:
                    returned_2 = True
                    returned_value = 0
                return returned_value

            for i in range(func1(a, b)):
                if i == b:
                    returned_1 = True
                    returned_value = i
                keepgoing = not returned_1
                if returned_1:
                    break

            if not returned_1:
                returned_1 = True
                returned_value = 0
            return returned_value

        value = 0
        for a in range(10):
            for b in range(10):
                value += func(a, b)
        """)
        orig_ast = gast.ast_to_gast(ast.parse(orig_code))
        target_ast = gast.ast_to_gast(ast.parse(target_code))
        converted_ast = self.canonicalizer.visit(orig_ast)

        assert_semantically_equals(orig_code, target_code, ['value'])
        assert compare_ast(converted_ast, target_ast)
        assert compare_ast(target_ast, converted_ast)


# ======================================

def main():
    unittest.main()

if __name__ == '__main__':
    main()