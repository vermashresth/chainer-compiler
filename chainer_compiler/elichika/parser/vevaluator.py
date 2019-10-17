import chainer
import chainer.functions as F
import chainer.links as L
import inspect
import ast, gast
import itertools
import numbers
from contextlib import ExitStack

from chainer_compiler.elichika.parser import config
from chainer_compiler.elichika.parser import nodes
from chainer_compiler.elichika.parser import values
from chainer_compiler.elichika.parser import functions
from chainer_compiler.elichika.parser import utils
from chainer_compiler.elichika.parser.graphs import Graph
from chainer_compiler.elichika.parser import veval_bin
from chainer_compiler.elichika.parser import veval_unary
from chainer_compiler.elichika.parser import veval_multiary
from chainer_compiler.elichika.parser import veval_aug_assign

import numpy as np


def get_ast_name_forcibly(ast):
    if isinstance(ast, gast.gast.Name):
        return ast.id
    if isinstance(ast, gast.gast.Attribute):
        return ast.attr
    if isinstance(ast, str):
        return ast
    return ''

def return_value_or_obj(obj : 'value.Object'):
    if isinstance(obj.get_value(), values.NumberValue):
        return values.Object(obj.get_value())

    if isinstance(obj.get_value(), values.StrValue):
        return values.Object(obj.get_value())

    if isinstance(obj.get_value(), values.BoolValue):
        return values.Object(obj.get_value())

    if isinstance(obj.get_value(), values.NoneValue):
        return values.Object(obj.get_value())

    if isinstance(obj.get_value(), values.TupleValue):
        return values.Object(obj.get_value())

    return obj
    
class AstContext:
    def __init__(self, nast, lineno_offset : 'int', filename : 'str' = '' ):
        self.nast = nast
        self.lineno_offset = lineno_offset
        self.lineno = self.lineno_offset
        self.filename = filename
        if hasattr(self.nast, 'lineno'):
            self.lineno = self.nast.lineno + self.lineno_offset

    def c(self, value) -> 'AstContext':
        """
        get AstContext including value
        """
        return AstContext(value, self.lineno_offset, filename=self.filename)

def veval_ast_attribute(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None) -> 'Attribute':
    assert(isinstance(astc.nast, gast.gast.Attribute))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    from_module = True
    if context is not None and context._eval_as_written_target:
        from_module = False

    value = veval_ast(astc.c(astc.nast.value), local_field, graph, context)
    value_ref = utils.try_get_obj(value, 'attribute', lineprop)

    if(value_ref is None):
        utils.print_warning('Unknown or disabled attribute "{}" is accessed'.format(get_ast_name_forcibly(astc.nast.value)), lineprop)
        return None

    attr = value_ref.get_field().get_attribute(astc.nast.attr, graph.root_graph, False)

    # property(getter)
    if attr.has_obj() and isinstance(attr.get_obj().get_value(), values.FuncValue) and attr.get_obj().get_value().func.is_property:
        func_value = attr.get_obj().get_value()
        ret = func_value.func.vcall(func_value.module, graph, func_value.obj, functions.FunctionArgInput(), context, lineprop)
        return ret

    if attr.has_obj():
        return attr

    # if attr is not found
    gotten_obj = value_ref.try_get_and_store_obj(astc.nast.attr, graph.root_graph)
    if gotten_obj is not None:
        return value_ref.get_field().get_attribute(astc.nast.attr, graph.root_graph, from_module)

    if context is not None and context._eval_as_written_target:
        return attr
        
    # value is unknown
    if value is None:
        utils.print_warning('Assigning value {} is not found'.format(get_ast_name_forcibly(astc.nast.value)), lineprop)
    else:
        utils.print_warning('Assigning value {} is not found'.format(get_ast_name_forcibly(astc.nast.attr)), lineprop)

    return None

def veval_ast_assign(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.Assign))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    value = veval_ast(astc.c(astc.nast.value), local_field, graph, context)
    value_obj = utils.try_get_obj(value, 'assign', lineprop)

    if value is None:
        if config.show_warnings:
            print('It is possible that assiging value is invalid in L.{}'.format(astc.lineno))
        return None

    with context.eval_as_written_target():
        targets = veval_ast(astc.c(astc.nast.targets[0]), local_field, graph, context)

    if isinstance(targets, list):
        # ex. a,b = (1,2)
        if not isinstance(value_obj.get_value(), values.TupleValue):
            # TODO fix it
            assert(False)   # not supported
        
        for i in range(len(targets)):
            assert(value_obj.get_value().get_constant_value() is not None)

            node_assign = nodes.NodeAssign(targets[i], value_obj.get_value().get_constant_value()[i], astc.lineno)
            targets[i].revise(utils.try_get_obj(value_obj.get_value().get_constant_value()[i],'assign', lineprop))
            graph.add_node(node_assign)
    else:
        assigned_obj = return_value_or_obj(value_obj)
        node_assign = nodes.NodeAssign(targets, assigned_obj, astc.lineno)
        targets.revise(assigned_obj)
        graph.add_node(node_assign)

def veval_ast_name(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None) -> 'Attribute':
    assert(isinstance(astc.nast, gast.gast.Name))

    from_module = True
    if context is not None and context._eval_as_written_target:
        from_module = False

    ret = local_field.get_attribute(astc.nast.id, graph.root_graph, from_module=from_module)
    return ret

def veval_ast_call(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None) -> 'Attribute':
    assert(isinstance(astc.nast, gast.gast.Call))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    func = veval_ast(astc.c(astc.nast.func), local_field, graph, context)
    if func == None or not func.has_obj():
        utils.print_warning('Unknown function "{}" is called'.format(get_ast_name_forcibly(astc.nast.func)), lineprop)
        return None

    func_obj = utils.try_get_obj(func, 'call', lineprop)
    func_value = utils.try_get_value(func, 'call', lineprop)

    finput = functions.FunctionArgInput()

    for arg in astc.nast.args:
        arg_ = veval_ast(astc.c(arg), local_field, graph, context)
        finput.inputs.append(utils.try_get_obj(arg_, 'call', lineprop))

    for keyword in astc.nast.keywords:
        arg_ = veval_ast(astc.c(keyword.value), local_field, graph, context)
        finput.keywords[keyword.arg] = utils.try_get_obj(arg_, 'call', lineprop)

    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    # check arguments
    for o in finput.inputs:
        if o is None:
            utils.print_warning('Invalid arguments exists in "{}"'.format(get_ast_name_forcibly(astc.nast.func)), lineprop)
            return None

    ret = None
    if isinstance(func_value, values.FuncValue):
        ret = func_value.func.vcall(func_value.module, graph, func_value.obj, finput, context, lineprop)
        return ret

    elif isinstance(func_value, values.Instance):
        # __call__
        call_func_ref = func_obj.try_get_and_store_obj('__call__', graph.root_graph)
        if call_func_ref is not None:
            func_value = call_func_ref.get_value()
            ret = func_value.func.vcall(func_value.module, graph, func_obj, finput, context, lineprop)
            return ret

    
    if config.show_warnings:
        print('Unknown function is called in L.{}'.format(astc.lineno))
    return None

    
def veval_ast_return(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None) -> 'None':
    assert(isinstance(astc.nast, gast.gast.Return))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    value = veval_ast(astc.c(astc.nast.value), local_field, graph, context)
    value_obj = utils.try_get_obj(value, 'return', lineprop)
    value_value = utils.try_get_value(value, 'return', lineprop)

    if value_value is None:
        if config.show_warnings:
            print('Returned values are not found. in L.{}'.format(astc.lineno))
        return None

    node = nodes.NodeReturn(value_value,astc.lineno)
    graph.add_node(node)
    return value

def veval_ast_if(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.If))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    # if condition
    test = veval_ast(astc.c(astc.nast.test), local_field, graph, context)
    test_value = utils.try_get_value(test, 'if', lineprop)

    id_str = str(utils.get_guid())
    if_id = 'if_' + id_str
    true_id = 'true_' + id_str
    false_id = 'false_' + id_str

    # True
    values.push_history(true_id)

    true_graph = Graph()
    true_graph.root_graph = graph.root_graph
    true_graph.name = 'True'
    # Skip true body when the test_value is decidably False.
    if test_value.has_constant_value() and test_value.internal_value == False:
        true_body = []
    else:
        true_body = veval_ast(astc.c(astc.nast.body), local_field, true_graph, context)

    true_value_inputs = values.get_inputs()
    true_value_outputs = values.get_outputs()

    values.pop_history()

    # False
    values.push_history(false_id)

    false_graph = Graph()
    false_graph.root_graph = graph.root_graph
    false_graph.name = 'False'
    # Skip false body when the test_value is decidably True.
    if test_value.has_constant_value() and test_value.internal_value == True:
        false_body = []
    else:
        false_body = veval_ast(astc.c(astc.nast.orelse), local_field, false_graph, context)

    false_value_inputs = values.get_inputs()
    false_value_outputs = values.get_outputs()

    values.pop_history()

    # generate pairs
    value_pairs = {}
    for v in true_value_inputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['true_input_value'] = v.input_value
        value_pairs[key]['true_input_body_value'] = v.value
        value_pairs[key]['true_input_obj'] = v.obj

    for v in true_value_outputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['true_output_body_value'] = v.value
        value_pairs[key]['true_output_obj'] = v.obj

    for v in false_value_inputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['false_input_value'] = v.input_value
        value_pairs[key]['false_input_body_value'] = v.value
        value_pairs[key]['false_input_obj'] = v.obj

    for v in false_value_outputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['false_output_body_value'] = v.value
        value_pairs[key]['false_output_obj'] = v.obj

    inputs = []
    outputs = []

    def get_input_value(v) -> "values.Value":
        if 'true_input_value' in v:
            return v['true_input_value']
        elif 'false_input_value' in v:
            return v['false_input_value']
        else:
            return None

    def get_body_input_value(v, input_value) -> "values.Value":
        if v is None:
            return (None, None)

        true_input_body_value = None
        false_input_body_value = None

        if 'true_input_body_value' in v:
            true_input_body_value = v['true_input_body_value']
        else:
            true_input_body_value = functions.generate_value_with_same_type(input_value)

        if 'false_input_body_value' in v:
            false_input_body_value = v['false_input_body_value']
        else:
            false_input_body_value = functions.generate_value_with_same_type(input_value)

        return (true_input_body_value, false_input_body_value)

    # collect inputs
    input_2_body_inputs = {}
    for k, v in value_pairs.items():
        input_value = get_input_value(v)

        if input_value is None:
            continue

        if not (input_value in input_2_body_inputs.keys()):
            body_input_value = get_body_input_value(v, input_value)
            input_2_body_inputs[input_value] = body_input_value

    for k, v in input_2_body_inputs.items():
        inputs.append(k)
        true_graph.add_input_value(v[0])
        false_graph.add_input_value(v[1])


    for k, v in value_pairs.items():
        name = v['name']
        field = v['field']

        input_value = get_input_value(v)

        true_input_body_value = None
        false_input_body_value = None

        if input_value in input_2_body_inputs.keys():
            true_input_body_value = input_2_body_inputs[input_value][0]
            false_input_body_value = input_2_body_inputs[input_value][1]

        true_output_body_value = None
        false_output_body_value = None
        output_value = None

        # search output value
        if 'true_output_body_value' in v:
            true_output_body_value = v['true_output_body_value']

        if 'false_output_body_value' in v:
            false_output_body_value = v['false_output_body_value']

        if true_output_body_value is not None or false_output_body_value is not None:

            if true_output_body_value is None:
                if true_input_body_value is not None:
                    # e.x. not changed
                    true_output_body_value = true_input_body_value
                else:
                    # e.x. make a value in false statement
                    true_output_body_value = functions.generate_value_with_same_type(false_output_body_value, is_dummy_value=True)

            if false_output_body_value is None:
                if false_input_body_value is not None:
                    # e.x. not changed
                    false_output_body_value = false_input_body_value
                else:
                    # e.x. make a value in true statement
                    false_output_body_value = functions.generate_value_with_same_type(true_output_body_value, is_dummy_value=True)

        # check types between true and false
        true_output_body_value_type = None
        false_output_body_value_type = None
        
        if true_output_body_value is not None and true_output_body_value.is_not_none_or_any_value():
            true_output_body_value_type = true_output_body_value

        if false_output_body_value is not None and false_output_body_value.is_not_none_or_any_value():
            false_output_body_value_type = false_output_body_value

        if true_output_body_value_type is not None and false_output_body_value_type is not None and type(true_output_body_value_type) != type(false_output_body_value_type):
            utils.print_warning('Values with differenet type were generated {} between true ande false'.format(k), lineprop)

        if true_output_body_value_type != None:
            output_value = functions.generate_value_with_same_type(true_output_body_value_type)
        elif false_output_body_value_type != None:
            output_value = functions.generate_value_with_same_type(false_output_body_value_type)
        elif true_output_body_value is not None:
            output_value = functions.generate_value_with_same_type(true_output_body_value)
        elif false_output_body_value is not None:
            output_value = functions.generate_value_with_same_type(false_output_body_value)

        if output_value is not None:
            outputs.append(output_value)
            true_graph.add_output_value(true_output_body_value)
            false_graph.add_output_value(false_output_body_value)
            
            if 'true_output_obj' in v and not 'false_output_obj' in v:
                obj = v['true_output_obj']
            elif not 'true_output_obj' in v and 'false_output_obj' in v:
                obj = v['false_output_obj']
            elif 'true_output_obj' in v and 'false_output_obj' in v:
                obj = None
            else:
                assert(False)

            if obj is not None:
                obj.revise(output_value)
                field.get_attribute(name).revise(obj)
            elif field.get_attribute(name).has_obj():
                field.get_attribute(name).get_obj().revise(output_value)
            else:
                field.get_attribute(name).revise(values.Object(output_value))

    node = nodes.NodeIf(test_value, inputs, true_graph, false_graph, astc.lineno)
    node.set_outputs(outputs)

    graph.add_node(node)

    return None

def veval_ast_aug_assign(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.AugAssign))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    target = veval_ast(astc.c(astc.nast.target), local_field, graph, context)
    value = veval_ast(astc.c(astc.nast.value), local_field, graph, context)

    target_value = utils.try_get_value(target, 'aug_assign', lineprop)
    value_value = utils.try_get_value(value, 'aug_assign', lineprop)

    binop = nodes.BinOpType.Unknown
    if isinstance(astc.nast.op, gast.Add):
        binop = nodes.BinOpType.Add
    elif isinstance(astc.nast.op, gast.Sub):
        binop = nodes.BinOpType.Sub
    elif isinstance(astc.nast.op, gast.Mult):
        binop = nodes.BinOpType.Mul
    elif isinstance(astc.nast.op, gast.Div):
        binop = nodes.BinOpType.Div
    elif isinstance(astc.nast.op, gast.FloorDiv):
        binop = nodes.BinOpType.FloorDiv
    else:
        utils.print_warning('Unknown binary operator {}'.format(astc.nast.op), lineprop)
        return None

    node_aug_assign = nodes.NodeAugAssign(target_value, value_value, binop, astc.lineno)
    graph.add_node(node_aug_assign)

    new_value = veval_aug_assign.veval(binop, target_value, value_value, lineprop)
    node_aug_assign.set_outputs([new_value])
    utils.try_get_obj(target, 'aug_assign', lineprop).revise(new_value)

def veval_ast_expr(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    call a function without not assigning
    Ex. b.x()
    '''
    assert(isinstance(astc.nast, gast.gast.Expr))
    return veval_ast(astc.c(astc.nast.value), local_field, graph, context)

def veval_ast_subscript(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    Ex. x[1], x[y,z]
    '''
    assert(isinstance(astc.nast, gast.gast.Subscript))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    def veval_with_default(nast, default_value):
        if nast is None:
            ret = values.NumberValue(default_value)
            ret.name = '@SliceDefault'
            return ret
        obj = veval_ast(astc.c(nast), local_field, graph, context)
        return utils.try_get_value(obj, 'subscript', lineprop)

    def get_slice_indices(slice):
        if slice.lower is None and slice.upper is None and slice.step is None:
            return []
        indices = [veval_with_default(slice.lower, 0),
                   veval_with_default(slice.upper, utils.slice_int_max)]
        if slice.step is not None:
            indices.append(veval_with_default(slice.step, 1))
        return indices

    value = veval_ast(astc.c(astc.nast.value), local_field, graph, context)
    value_value = utils.try_get_value(value, 'subscript', lineprop)

    if isinstance(value_value, values.DictValue):

        if isinstance(astc.nast.slice, gast.gast.Index):
            slice_ = veval_ast(astc.c(astc.nast.slice.value), local_field, graph, context)
            slice_value = utils.try_get_value(slice_, 'subscript', lineprop)
            value_value.internal_keys[slice_value.encode()] = slice_
            ret = value_value.internal_values.get_attribute(slice_value.encode())
            return ret
    elif isinstance(value_value, values.Instance):
        if isinstance(astc.nast.slice, gast.gast.Index):
            slice_ = veval_ast(astc.c(astc.nast.slice.value), local_field, graph, context)

            finput = functions.FunctionArgInput()
            finput.inputs.append(slice_)

            value_ref = utils.try_get_obj(value, 'subscript', lineprop)
            getitem_func = value_ref.get_field().get_attribute('__getitem__', graph.root_graph, False)
            getitem_func_value = getitem_func.get_obj().get_value()
            ret = getitem_func_value.func.vcall(getitem_func_value.module, graph, getitem_func_value.obj, finput, context, lineprop)
            return ret
    elif isinstance(value_value, (values.ListValue, values.TupleValue, values.TensorValue)):
        if isinstance(astc.nast.slice, gast.gast.Index):
            slice_ = veval_ast(astc.c(astc.nast.slice.value), local_field, graph, context)
            slice_value = utils.try_get_value(slice_, 'subscript', lineprop)

            if isinstance(slice_value, values.TupleValue):
                # ex. x[1,2]
                if slice_value.has_constant_value():
                    values_ = [utils.try_get_value(x, 'subscript', lineprop) for x in slice_value.get_constant_value()]
                    node = nodes.NodeGetItem(value_value, values_, line=lineprop)
                else:
                    if config.show_warnings:
                        print('This subscript is not supported. in L.{}'.format(astc.lineno))
                    node = nodes.NodeInvalid(line=lineprop)
            else:
                # ex. x[1]
                node = nodes.NodeGetItem(value_value, [slice_value])

            if isinstance(value_value, values.TensorValue):
                ret_value = values.TensorValue()
            else:
                if value_value.vtype != None and issubclass(value_value.vtype, values.Instance):
                    assert value_value.has_constant_value()
                    assert slice_value.has_constant_value()
                    return value_value.internal_value[slice_value.internal_value]
                elif value_value.vtype != None:
                    ret_value = value_value.vtype(None)
                    ret_value.dtype = value_value.dtype
                else:
                    utils.print_warning("Unable to determine element type of {}. Using TensorValue as default.".format(value_value), lineprop)
                    ret_value = values.TensorValue()

            node.set_outputs([ret_value])
            graph.add_node(node)
            if isinstance(value, values.Attribute):
                ret_attr = value.make_subscript_attribute(slice_, graph)
                ret_attr.revise(values.Object(ret_value), update_parent=False)
                return ret_attr
            else:
                return values.Object(ret_value)

        elif isinstance(astc.nast.slice, gast.gast.Slice):

            indices = get_slice_indices(astc.nast.slice)

            node = nodes.NodeSlice(value_value, indices, [len(indices)])
            ret_value = functions.generate_value_with_same_type(value_value)

            # for constant propagation, populate ret_value when possible
            if value_value.has_constant_value():
                if all([value.has_constant_value() for value in indices]):
                    start, end = (indice.internal_value for indice in indices[:2])
                    step = indices[2].internal_value if len(indices) == 3 else None
                    ret_value.internal_value = value_value.internal_value[start:end:step]

            node.set_outputs([ret_value])
            graph.add_node(node)
            return values.Object(ret_value)

        elif isinstance(astc.nast.slice, gast.gast.ExtSlice):
            indices = []
            slice_specs = []
            for dim in astc.nast.slice.dims:
                if isinstance(dim, gast.gast.Index):
                    indices.append(utils.try_get_value(veval_ast(astc.c(dim.value), local_field, graph, context), 'subscript', lineprop))
                    slice_specs.append(1)
                elif isinstance(dim, gast.gast.Slice):
                    ni = get_slice_indices(dim)
                    indices.extend(ni)
                    slice_specs.append(len(ni))
                else:
                    assert False, 'Unknown slice: %s in %s' % (dim, nast.slice)

            node = nodes.NodeSlice(value_value, indices, slice_specs)
            ret_value = functions.generate_value_with_same_type(value_value)
            node.set_outputs([ret_value])
            graph.add_node(node)
            return values.Object(ret_value)
    else:
        utils.print_warning("Subscript not possible for type {}".format(type(value_value)), lineprop)

    return None

def veval_ast_listcomp(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    Ex. [x for x in xx]
    [elt for target in iter]
    '''
    assert(isinstance(astc.nast, gast.gast.ListComp))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    listcomp_guid = str(utils.get_guid())
    listcomp_id = 'listcomp_' + listcomp_guid
    body_id = 'listcomp_body_' + listcomp_guid
    internal_counter_id = '@internal/listcomp_counter_' + listcomp_guid
    internal_list_id = '@internal/listcomp_list_' + listcomp_guid
    internal_cond_id = '@internal/listcomp_cond_' + listcomp_guid

    generator = astc.nast.generators[0]
    iter_value = utils.try_get_value(veval_ast(astc.c(generator.iter), local_field, graph, context), 'generator', lineprop)
    list_value = values.ListValue()
    list_obj = values.Object(list_value)

    node_generate_list = nodes.NodeGenerate('List', [], lineprop)
    node_generate_list.set_outputs([list_value])
    graph.add_node(node_generate_list)

    # body
    target_name = ''
    if isinstance(generator.target, gast.gast.Name):
        target_name = generator.target.id
    else:
        if config.show_warnings:
            print('This for is not supported. in L.{}'.format(astc.lineno))
        return None

    counter_value = values.NumberValue(None)
    counter_value.dtype = np.array(0).dtype
    counter_value.name = internal_counter_id

    cond_value = values.BoolValue(None)
    cond_value.name = internal_cond_id

    # set values with internal name
    local_field.get_attribute(internal_list_id).revise(list_obj)

    values.push_history(listcomp_id)

    body_graph = Graph()
    body_graph.root_graph = graph.root_graph
    body_graph.name = 'Body_' + listcomp_guid

    node_forgen = nodes.NodeForGenerator(counter_value, iter_value)

    target_obj = iter_value.get_iterator()
    if target_obj is None:
        target_obj = values.Object(values.UnknownValue())
        if config.show_warnings:
            print('unknown iteratable type in L.{}'.format(lineprop))
    target_value = target_obj.get_value()

    node_forgen.set_outputs([target_obj.get_value()])
    local_field.get_attribute(target_name).revise(target_obj)
    
    body_graph.add_node(node_forgen)

    elt = veval_ast(astc.c(astc.nast.elt), local_field, body_graph, context)
    elt_obj = utils.try_get_obj(elt, 'listcomp', lineprop)

    finput = functions.FunctionArgInput()
    finput.inputs.append(elt_obj)

    append_value = local_field.get_attribute(internal_list_id).get_obj().get_field().get_attribute('append').get_obj().get_value()
    append_value.func.vcall(None, body_graph, local_field.get_attribute(internal_list_id).get_obj(), finput, context, lineprop)

    value_inputs = values.get_inputs()
    value_outputs = values.get_outputs()

    values.pop_history()

    inputs = []
    outputs = []

    # default input for subgraph's input
    body_graph.add_input_value(counter_value)
    body_graph.add_input_value(cond_value)
    body_graph.add_input_value(iter_value)

    # default output for subgraph's output
    body_graph.add_output_value(cond_value)
    body_graph.add_output_value(iter_value)

    # default output
    outputs.append(functions.generate_value_with_same_type(iter_value))
    
    # generate pairs
    value_pairs = {}
    for v in value_inputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['input_value'] = v.input_value
        value_pairs[key]['input_body_value'] = v.value

    for v in value_outputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['output_body_value'] = v.value
        value_pairs[key]['output_obj'] = v.obj

    # remove iterator
    removed_name = str(local_field.id) + '_' + target_value.name
    del value_pairs[removed_name]

    for k, v in value_pairs.items():
        name = v['name']
        field = v['field']

        if 'input_body_value' in v:
            inputs.append(v['input_value'])
            body_graph.add_input_value(v['input_body_value'])

        else:
            temp_value1 = functions.generate_value_with_same_type(v['output_body_value'])
            temp_value2 = functions.generate_value_with_same_type(v['output_body_value'])
            inputs.append(temp_value1)
            body_graph.add_input_value(temp_value2)

        if 'output_body_value' in v:
            body_graph.add_output_value(v['output_body_value'])
            output_value = functions.generate_value_with_same_type(v['output_body_value'])
            outputs.append(output_value)
            if 'output_obj' in v:
                obj = v['output_obj']
                obj.revise(output_value)
                field.get_attribute(name).revise(obj)
            elif field.get_attribute(name).has_obj():
                field.get_attribute(name).get_obj().revise(output_value)
            else:
                field.get_attribute(name).revise(values.Object(output_value))
        else:
            temp_value1 = v['input_body_value']
            temp_value2 = functions.generate_value_with_same_type(v['input_body_value'])
            body_graph.add_output_value(temp_value1)
            outputs.append(temp_value2)

    node = nodes.NodeListcomp(iter_value, inputs, body_graph, astc.lineno)
    node.set_outputs(outputs)

    graph.add_node(node)

    return local_field.get_attribute(internal_list_id).get_obj()

def veval_ast_bin_op(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    """
    eval binary operation.
    Ex. a + b, b // c, etc
    """
    assert(isinstance(astc.nast, gast.gast.BinOp))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    left = veval_ast(astc.c(astc.nast.left), local_field, graph, context)
    right = veval_ast(astc.c(astc.nast.right), local_field, graph, context)

    left_value = utils.try_get_value(left, 'compare', lineprop)
    right_value = utils.try_get_value(right, 'compare', lineprop)

    binop = nodes.BinOpType.Unknown
    if isinstance(astc.nast.op, gast.Add):
        binop = nodes.BinOpType.Add
    elif isinstance(astc.nast.op, gast.Sub):
        binop = nodes.BinOpType.Sub
    elif isinstance(astc.nast.op, gast.Mult):
        binop = nodes.BinOpType.Mul
    elif isinstance(astc.nast.op, gast.Div):
        binop = nodes.BinOpType.Div
    elif isinstance(astc.nast.op, gast.FloorDiv):
        binop = nodes.BinOpType.FloorDiv
    elif isinstance(astc.nast.op, gast.Mod):
        binop = nodes.BinOpType.Mod
    else:
        utils.print_warning('Unknown binary operator {}'.format(astc.nast.op), lineprop)
        return None

    node_bin_op = nodes.NodeBinOp(left_value, right_value, binop, astc.lineno)

    ret_value = veval_bin.veval(binop, left_value, right_value, lineprop)

    node_bin_op.set_outputs([ret_value])
    graph.add_node(node_bin_op)

    return values.Object(ret_value)

def veval_ast_bool_op(astc : 'AstContext', local_field : 'values.Field', graph : 'graphs.Graph', context : 'functions.VEvalContext' = None):
    """
    eval bool operations.
    Ex. x and y
    """
    assert(isinstance(astc.nast, gast.gast.BoolOp))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    multiaryop = nodes.MultiaryOpType.Unknown
    if isinstance(astc.nast.op, gast.And):
        multiaryop = nodes.MultiaryOpType.And
    if isinstance(astc.nast.op, gast.Or):
        multiaryop = nodes.MultiaryOpType.Or

    values_list = [veval_ast(astc.c(value_), local_field, graph, context) for value_ in astc.nast.values]
    values_list_value = [utils.try_get_value(value_, 'multiary', lineprop) for value_ in values_list]

    node = nodes.NodeMultiaryOp(values_list_value, multiaryop)

    ret_value = veval_multiary.veval(multiaryop, values_list_value)
    node.set_outputs([ret_value])
    graph.add_node(node)

    return values.Object(ret_value)

def veval_ast_unary_op(astc : 'AstContext', local_field : 'values.Field', graph : 'graphs.Graph', context : 'functions.VEvalContext' = None):
    """
    eval unary operation.
    Ex. -xx
    """
    assert(isinstance(astc.nast, gast.gast.UnaryOp))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    unaryop = nodes.UnaryOpType.Unknown
    if isinstance(astc.nast.op, gast.UAdd):
        unaryop = nodes.UnaryOpType.UAdd
    if isinstance(astc.nast.op, gast.USub):
        unaryop = nodes.UnaryOpType.USub
    if isinstance(astc.nast.op, gast.Not):
        unaryop = nodes.UnaryOpType.Not

    operand = veval_ast(astc.c(astc.nast.operand), local_field, graph, context)
    operand_value = utils.try_get_value(operand, 'unary', lineprop)

    node = nodes.NodeUnaryOp(operand_value, unaryop)

    ret_value = veval_unary.veval(unaryop, operand_value)

    node.set_outputs([ret_value])
    graph.add_node(node)

    return values.Object(ret_value)


def veval_ast_compare(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    """
    eval Compare.
    Ex. a >= b, a != b, a is b, etc
    """
    assert(isinstance(astc.nast, gast.gast.Compare))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    left = veval_ast(astc.c(astc.nast.left), local_field, graph, context)
    right = veval_ast(astc.c(astc.nast.comparators[0]), local_field, graph, context)

    left_value = utils.try_get_value(left, 'compare', lineprop)
    right_value = utils.try_get_value(right, 'compare', lineprop)

    compare = nodes.CompareType.unknown
    if isinstance(astc.nast.ops[0], gast.Eq):
        compare = nodes.CompareType.Eq
    if isinstance(astc.nast.ops[0], gast.NotEq):
        compare = nodes.CompareType.NotEq
    if isinstance(astc.nast.ops[0], gast.Is):
        compare = nodes.CompareType.Is
    if isinstance(astc.nast.ops[0], gast.IsNot):
        compare = nodes.CompareType.IsNot
    if isinstance(astc.nast.ops[0], gast.Gt):
        compare = nodes.CompareType.Gt
    if isinstance(astc.nast.ops[0], gast.GtE):
        compare = nodes.CompareType.GtE
    if isinstance(astc.nast.ops[0], gast.Lt):
        compare = nodes.CompareType.Lt
    if isinstance(astc.nast.ops[0], gast.LtE):
        compare = nodes.CompareType.LtE
    if isinstance(astc.nast.ops[0], gast.In):
        compare = nodes.CompareType.In
    if isinstance(astc.nast.ops[0], gast.NotIn):
        compare = nodes.CompareType.NotIn

    node_compare = nodes.NodeCompare(left_value, right_value, compare, astc.lineno)

    # constant propagation when possible
    default_value = None
    if left_value.has_constant_value() and right_value.has_constant_value():
        if isinstance(astc.nast.ops[0], gast.Eq):
            default_value = left_value.internal_value == right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.NotEq):
            default_value = left_value.internal_value != right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.Is):
            default_value = left_value.internal_value is right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.IsNot):
            default_value = left_value.internal_value is not right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.Gt):
            default_value = left_value.internal_value > right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.GtE):
            default_value = left_value.internal_value >= right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.Lt):
            default_value = left_value.internal_value < right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.LtE):
            default_value = left_value.internal_value <= right_value.internal_value
        if isinstance(astc.nast.ops[0], gast.In):
            default_value = left_value.internal_value in map(lambda ref: ref.get_value().internal_value, right_value.internal_value)
        if isinstance(astc.nast.ops[0], gast.NotIn):
            default_value = left_value.internal_value not in map(lambda ref: ref.get_value().internal_value, right_value.internal_value)

    ret_value = values.BoolValue(default_value)
    ret_value.name = '@{}'.format(lineprop)
    node_compare.set_outputs([ret_value])
    graph.add_node(node_compare)

    return values.Object(ret_value)


def veval_ast_num(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    Ex. 1, 2, ...
    '''
    assert(isinstance(astc.nast, gast.gast.Constant))
    assert(isinstance(astc.nast.value, numbers.Number))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)
    value = values.NumberValue(astc.nast.value)
    ret = values.Object(value)

    name = values.create_ref_value_name_with_constant(ret)
    ret.name = name
    ret.get_value().name = name
    return ret

def veval_ast_str(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    Ex. "str"
    '''
    assert(isinstance(astc.nast, gast.gast.Constant))
    assert(isinstance(astc.nast.value, str))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)
    value = values.StrValue(astc.nast.value)
    ret = values.Object(value)

    name = values.create_ref_value_name_with_constant(ret)
    ret.name = name
    ret.get_value().name = name
    return ret

def veval_ast_name_constant(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    Ex. True
    '''
    assert(isinstance(astc.nast, gast.gast.Constant))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)
    ret = None
    if astc.nast.value == True:
        ret = values.Object(values.BoolValue(True))
    elif astc.nast.value == False:
        ret = values.Object(values.BoolValue(False))
    elif astc.nast.value is None:
        ret = values.Object(values.NoneValue())
    else:
        print("Invalid name constant: {}".format(astc.nast.value))
        assert False

    name = values.create_ref_value_name_with_constant(ret)
    ret.name = name
    ret.get_value().name = name
    return ret

def veval_ast_tuple(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.Tuple))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    if context is not None and context._eval_as_written_target:
        vs = []
        for v in astc.nast.elts:
            a_ = veval_ast(astc.c(v), local_field, graph, context=context)
            vs.append(a_)
        return vs
    else:
        vs_ref = []
        vs = []

        for v in astc.nast.elts:
            a_ = veval_ast(astc.c(v), local_field, graph, context=context)
            v_ = utils.try_get_obj(a_, 'tuple', lineprop)
            
            if v_ is None:
                utils.print_warning('Unknown tuple element {}'.format(v), lineprop)
                return None

            vs_ref.append(v_)
            vs.append(v_.get_value())
            v_.in_container = True

        tuple_value = values.TupleValue(vs_ref)

        node = nodes.NodeGenerate('Tuple', vs, line=lineprop)
        node.set_outputs([tuple_value])
        graph.add_node(node)
        
        return values.Object(tuple_value)

def veval_ast_list(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.List))
    '''
    Ex. [],[x,y,z]
    TODO : Initializer
    '''
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    elts = []
    for elt in astc.nast.elts:
        elt_ = veval_ast(astc.c(elt), local_field, graph, context)
        elt_obj = utils.try_get_obj(elt_,'list', lineprop)
        elts.append(elt_obj)

    node = nodes.NodeGenerate('List', [elt.get_value() for elt in elts], lineprop)
    graph.add_node(node)
    value = values.ListValue(elts)
    node.set_outputs([value])

    return values.Object(value)

def veval_ast_dict(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.Dict))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    keys = []
    elts = []

    for key, elt in zip(astc.nast.keys, astc.nast.values):
        key_ = veval_ast(astc.c(key), local_field, graph, context)
        elt_ = veval_ast(astc.c(elt), local_field, graph, context)
        key_obj = utils.try_get_obj(key_, 'dict', lineprop)
        elt_obj = utils.try_get_obj(elt_,'dict', lineprop)
        keys.append(key_obj)
        elts.append(return_value_or_obj(elt_obj))

    value = values.DictValue(keys, elts)

    return values.Object(value)

def veval_ast_for_unroll(astc : 'AstContext', target_name, iter_ : 'values.ListValue', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    for target in iter: 
        ...
    with unroll
    '''
    assert(isinstance(astc.nast, gast.gast.For))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    for element in iter_.get_constant_value():
        local_field.get_attribute(target_name).revise(element)
        veval_ast(astc.c(astc.nast.body), local_field, graph, context)
    
    return None

def veval_ast_for(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    for target in iter:
        ...
    '''
    assert(isinstance(astc.nast, gast.gast.For))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    # for target in iter:
    iter_ = veval_ast(astc.c(astc.nast.iter), local_field, graph, context)
    input_iter_value = utils.try_get_value(iter_, 'for', lineprop)
    body_iter_value = functions.generate_value_with_same_type(input_iter_value, suffix_type=functions.SuffixType.Input)

    # get target name
    target_name = ''
    if isinstance(astc.nast.target, gast.gast.Name):
        target_name = astc.nast.target.id
    else:
        if config.show_warnings:
            print('This for is not supported. in L.{}'.format(astc.lineno))
        return None

    # unroll?
    if isinstance(input_iter_value, values.ListValue) and input_iter_value.has_constant_value() and input_iter_value.dtype is None:
        return veval_ast_for_unroll(astc, target_name, input_iter_value, local_field, graph, context)

    for_guid = utils.get_guid()
    for_id = 'for_' + str(for_guid)
    body_id = 'body_' + str(for_guid)

    values.push_history(for_id)

    # body
    body_graph = Graph()
    body_graph.root_graph = graph.root_graph
    body_graph.name = 'Body_' + str(for_guid)

    # generate a node for input
    node_input = nodes.NodeInput('input')
    body_graph.add_node(node_input)

    body_counter_value = values.NumberValue(None)
    body_counter_value.dtype = np.array(0).dtype
    body_counter_value.name = 'for_counter_' + str(for_guid)

    body_cond_value = values.BoolValue(None)
    body_cond_value.name = 'for_cond_' + str(for_guid)
    
    # create a node to lookup a value from sequence
    node_forgen = nodes.NodeForGenerator(body_counter_value, body_iter_value)

    # generate iterator
    target_obj = input_iter_value.get_iterator()
    if target_obj is None:
        target_obj = values.Object(values.UnknownValue())
        if config.show_warnings:
            print('unknown iteratable type in L.{}'.format(astc.lineno))
    target_value = target_obj.get_value()
    
    node_forgen.set_outputs([target_obj.get_value()])

    target_attribute = local_field.get_attribute(target_name)
    target_attribute.revise(target_obj)
    body_graph.add_node(node_forgen)

    # veval body
    body = veval_ast(astc.c(astc.nast.body), local_field, body_graph, context)

    value_inputs = values.get_inputs()
    value_outputs = values.get_outputs()

    break_attribute = local_field.get_attribute('#keepgoing')
    if break_attribute.has_obj():
        break_attribute_ref = break_attribute.get_obj()
        break_attribute_value = break_attribute_ref.get_value()
    else:
        break_attribute_value = body_cond_value 

    values.pop_history()

    inputs = []
    outputs = []
    node_input_outputs = []

    # default input for subgraph's input
    body_graph.add_input_value(body_counter_value)
    body_graph.add_input_value(body_cond_value)
    body_graph.add_input_value(body_iter_value)

    # default output for subgraph's output
    body_graph.add_output_value(break_attribute_value)
    body_graph.add_output_value(body_iter_value)

    # default output
    outputs.append(functions.generate_value_with_same_type(input_iter_value))
    
    # generate pairs
    value_pairs = {}
    for v in value_inputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['input_value'] = v.input_value
        value_pairs[key]['input_body_value'] = v.value

    for v in value_outputs:
        key = str(v.field.id) + '_' + v.name
        if not (key in value_pairs.keys()):
            value_pairs[key] = {}

        value_pairs[key]['field'] = v.field
        value_pairs[key]['name'] = v.name
        value_pairs[key]['output_body_value'] = v.value
        value_pairs[key]['output_obj'] = v.obj

    for k, v in value_pairs.items():
        name = v['name']
        field = v['field']

        if 'input_body_value' in v:
            inputs.append(v['input_value'])

            body_graph.add_input_value(v['input_body_value'])
        else:
            temp_value1 = functions.generate_value_with_same_type(v['output_body_value'], is_dummy_value=True, suffix_type=functions.SuffixType.Dummy)
            temp_value2 = functions.generate_value_with_same_type(v['output_body_value'], suffix_type=functions.SuffixType.Dummy)
            inputs.append(temp_value1)

            body_graph.add_input_value(temp_value2)
            node_input_outputs.append(temp_value2)

        if 'output_body_value' in v:
            body_graph.add_output_value(v['output_body_value'])
            output_value = functions.generate_value_with_same_type(v['output_body_value'])
            outputs.append(output_value)

            if 'output_obj' in v:
                obj = v['output_obj']
                obj.revise(output_value)
                field.get_attribute(name).revise(obj)
            elif field.get_attribute(name).has_obj():
                field.get_attribute(name).get_obj().revise(output_value)
            else:
                field.get_attribute(name).revise(values.Object(output_value))
        else:
            temp_value1 = v['input_body_value']
            temp_value2 = functions.generate_value_with_same_type(v['input_body_value'])
            body_graph.add_output_value(temp_value1)
            outputs.append(temp_value2)

    node = nodes.NodeFor(input_iter_value, inputs, body_graph, body_cond_value, astc.lineno)
    node.set_outputs(outputs)
    node_input.set_outputs(node_input_outputs)

    graph.add_node(node)

    return None

def veval_ast_continue(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.Continue))
    return None

def veval_ast_break(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.Break))
    return None

def veval_ast_with(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.With))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    from_module = True
    if context is not None and context._eval_as_written_target:
        from_module = False

    context.flags_cache.clear()

    exit_attrs = []
    for item in astc.nast.items:
        item_ref = veval_ast(astc.c(item), local_field, graph, context)
        exit_attr = item_ref.get_field().get_attribute('__exit__', graph.root_graph, from_module)
        exit_attrs.append(exit_attr)

    with ExitStack() as stack:
        managers = [stack.enter_context(getattr(context, flag)(*args)) for flag, args in context.flags_cache]
        if not context._ignore_branch:
            veval_ast(astc.c(astc.nast.body), local_field, graph, context)

    for attr in exit_attrs:
        if attr.has_obj() and isinstance(attr.get_obj().get_value(), values.FuncValue):
            func_value = attr.get_obj().get_value()
            finput = functions.FunctionArgInput()

            # Adding exception_type, exception_value & traceback dummy arguments (None)
            finput.inputs.extend([values.Object(values.NoneValue())] * 3)
            func_value.func.vcall(func_value.module, graph, func_value.obj, finput, context, lineprop)


def veval_ast_withitem(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.withitem))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    from_module = True
    if context is not None and context._eval_as_written_target:
        from_module = False

    value = veval_ast(astc.c(astc.nast.context_expr), local_field, graph, context)
    value_obj = utils.try_get_obj(value, 'withitem', lineprop)

    enter_attr = value_obj.get_field().get_attribute('__enter__', graph.root_graph, from_module)
    if enter_attr.has_obj() and isinstance(enter_attr.get_obj().get_value(), values.FuncValue):
        func_value = enter_attr.get_obj().get_value()
        value_obj = func_value.func.vcall(func_value.module, graph, func_value.obj, functions.FunctionArgInput(), context, lineprop)
        value_obj = utils.try_get_obj(value_obj, 'withitem', lineprop)

    if value is None:
        if config.show_warnings:
            print('It is possible that one of those withitem is invalid in L.{}'.format(astc.lineno))
        return None

    value_obj = return_value_or_obj(value_obj)

    if astc.nast.optional_vars is not None:
        with context.eval_as_written_target():
            optional_vars = veval_ast(astc.c(astc.nast.optional_vars), local_field, graph, context)

        node_assign = nodes.NodeAssign(optional_vars, value_obj, astc.lineno)
        optional_vars.revise(value_obj)
        graph.add_node(node_assign)

    return value_obj

def veval_ast_lambda(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    '''
    lambda x, y, z=2: ...
    Note: kwonly_args are not supported
    '''
    assert(isinstance(astc.nast, gast.gast.Lambda))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    lambda_id = 'lambda_' + str(utils.get_guid())
    values.push_history(lambda_id)
    args = veval_ast(astc.c(astc.nast.args), local_field, graph, context)
    func = functions.UserDefinedFunctionFromAst(astc, args, local_field)
    values.pop_history()

    return values.Object(values.FuncValue(func, None))

def veval_ast_arguments(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    assert(isinstance(astc.nast, gast.gast.arguments))
    lineprop = utils.LineProperty(astc.lineno, astc.filename)

    ret = functions.FunctionArgCollection()

    argspec = inspect.FullArgSpec(astc.nast.args, astc.nast.vararg, astc.nast.kwarg,
                                  astc.nast.defaults, astc.nast.kwonlyargs, astc.nast.kw_defaults, None)

    assert not argspec.kwonlyargs, "Keyword only args are not supported"
    assert not argspec.varargs, "Varaibale arguments *args is not supported"
    assert not argspec.varkw, "Variable keywords **kwargs is not supported"

    defaults = [veval_ast(astc.c(default), local_field, graph, context) for default in argspec.defaults]
    arg_list = []
    for k, v in itertools.zip_longest(reversed(argspec.args), defaults):
        arg_list.append((k.id, v))

    # reverse the list
    for k, v in reversed(arg_list):
        ret.add_arg(k, v)

    return ret

def veval_ast(astc : 'AstContext', local_field : 'values.Field', graph : 'Graph', context : 'functions.VEvalContext' = None):
    if context is None:
        context = functions.VEvalContext()

    if isinstance(astc.nast, list):
        ret = None
        for nast_ in astc.nast:
            ret = veval_ast(AstContext(nast_, astc.lineno_offset, filename=astc.filename), local_field, graph, context)
            if ret is not None:
                break
        return ret

    elif isinstance(astc.nast, gast.gast.Assign):
        veval_ast_assign(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.Attribute):
        ret = veval_ast_attribute(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Call):
        ret = veval_ast_call(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.BinOp):
        ret = veval_ast_bin_op(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.UnaryOp):
        ret = veval_ast_unary_op(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Compare):
        ret = veval_ast_compare(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Return):
        ret = veval_ast_return(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Name):
        ret = veval_ast_name(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.AugAssign):
        veval_ast_aug_assign(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.Expr):
        veval_ast_expr(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.Subscript):
        return veval_ast_subscript(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.ListComp):
        return veval_ast_listcomp(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.If):
        veval_ast_if(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.Constant) and (type(astc.nast.value) == bool or astc.nast.value is None):
        return veval_ast_name_constant(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.Constant) and isinstance(astc.nast.value, numbers.Number):
        return veval_ast_num(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.Constant) and isinstance(astc.nast.value, str):
        return veval_ast_str(astc, local_field, graph, context)

    elif isinstance(astc.nast, gast.gast.Tuple):
        ret = veval_ast_tuple(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.List):
        ret = veval_ast_list(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.For):
        veval_ast_for(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.Continue):
        veval_ast_continue(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.Break):
        veval_ast_break(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.BoolOp):
        ret = veval_ast_bool_op(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.With):
        veval_ast_with(astc, local_field, graph, context)
        return None

    elif isinstance(astc.nast, gast.gast.withitem):
        ret = veval_ast_withitem(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Dict):
        ret = veval_ast_dict(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.Lambda):
        ret = veval_ast_lambda(astc, local_field, graph, context)
        return ret

    elif isinstance(astc.nast, gast.gast.arguments):
        ret = veval_ast_arguments(astc, local_field, graph, context)
        return ret

    else:
        if config.show_warnings:
            print('Unknown ast is found : {} in L.{}'.format(astc.nast, astc.lineno))
