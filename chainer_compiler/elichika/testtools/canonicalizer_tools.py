import ast, gast

def compare_ast(node1, node2):
    if type(node1) is not type(node2):
        return False
    if isinstance(node1, gast.AST):
        for k, v in vars(node1).items():
            if k in ('lineno', 'col_offset', 'ctx'):
                continue
            if not compare_ast(v, getattr(node2, k)):
                return False
        return True
    elif isinstance(node1, list):
        return all(compare_ast(n1, n2) for n1, n2 in zip(node1, node2))
    else:
        return node1 == node2

def assert_semantically_equals(orig_code, target_code, result_keys):
    exec(orig_code)
    orig_results = locals().copy()
    exec(target_code)
    target_results = locals().copy()
    for key in result_keys:
        assert orig_results[key] == target_results[key]