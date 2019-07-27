from chainer_compiler.elichika.parser import nodes
from chainer_compiler.elichika.parser import values
from chainer_compiler.elichika.parser import functions
from chainer_compiler.elichika.parser import graphs
from chainer_compiler.elichika.parser import utils
from chainer_compiler.elichika.parser import veval_multiary

import chainer
import chainer.functions as F
import chainer.links as L

def create_return_value_in_chainer_function():
    return values.TensorValue()

class ChainerFunction(functions.FunctionBase):
    def __init__(self, func, ret_value_func = create_return_value_in_chainer_function):
        super().__init__()
        self.name = func.__name__
        self.args.analyze_args(func)
        self.base_func = func
        self.ret_value_func = ret_value_func

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        funcArgs = self.args.merge_inputs(inst, args)

        node = nodes.NodeCall(self, funcArgs, line)
        graph.add_node(node)
        #value = functions.generate_value_with_same_type(vargs[0])
        value = self.ret_value_func()
        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        return values.Object(value)

class CopyFunction(functions.FunctionBase):
    def __init__(self, func):
        super().__init__()
        self.name = str(func)

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        node = nodes.NodeCopy(args.inputs[0].get_value())
        graph.add_node(node)
        ret = functions.generate_copied_value(args.inputs[0].get_value())
        node.set_outputs([ret])
        return values.Object(ret)

class RangeFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'range'

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):

        if option._for_unroll:
            for ref in args.inputs:
                if not ref.get_value().has_constant_value():
                    assert False, 'Loop unrolling was requested for non-constant sequence at %s' % line

            refs = []
            for num in range(*(ref.get_value().internal_value for ref in args.inputs)):
                refs.append(values.Object(values.NumberValue(num)))

            value = values.ListValue(refs)
            return values.Object(value)
        else:
            node = nodes.NodeGenerate(
                'range', [v.get_value() for v in args.inputs], line)
            graph.add_node(node)
            value = values.RangeValue()
            value.name = '@F.{}.{}'.format(line, self.name)
            node.set_outputs([value])
            return values.Object(value)


class LenFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'len'

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        node = nodes.NodeCall(self, args, line)
        graph.add_node(node)
        item = args.get_value().inputs[0]
        default_value = None
        # constant propagation whenever possible
        if item.has_constant_value():
            default_value = len(item.internal_value)

        value = values.NumberValue(default_value)
        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        return values.Object(value)


class MinFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'min'
        self.aggregateop = nodes.AggregateOpType.Min

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        if len(args.inputs) == 1 and isinstance(args.get_value().inputs[0], values.ListValue):
            list_value = args.get_value().inputs[0]
            if list_value.internal_value:
                values_list = [ref.get_value() for ref in list_value.internal_value]
            else:
                values_list = []
        else:
            values_list = args.get_value().inputs
            node = nodes.NodeGenerate('List', values_list, line)
            list_value = values.ListValue([utils.try_get_obj(ref, self.name, line) for ref in args.inputs])
            node.set_outputs([list_value])
            graph.add_node(node)

        node = nodes.NodeAggregate(list_value, self.aggregateop, line)
        value = veval_multiary.veval(self.aggregateop, values_list, line)
        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        graph.add_node(node)
        return values.Object(value)

class MaxFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'max'
        self.aggregateop = nodes.AggregateOpType.Max

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        if len(args.inputs) == 1 and isinstance(args.get_value().inputs[0], values.ListValue):
            list_value = args.get_value().inputs[0]
            if list_value.internal_value:
                values_list = [ref.get_value() for ref in list_value.internal_value]
            else:
                values_list = []
        else:
            values_list = args.get_value().inputs
            node = nodes.NodeGenerate('List', values_list, line)
            list_value = values.ListValue([utils.try_get_obj(ref, self.name, line) for ref in args.inputs])
            node.set_outputs([list_value])
            graph.add_node(node)

        node = nodes.NodeAggregate(list_value, self.aggregateop, line)
        value = veval_multiary.veval(self.aggregateop, values_list, line)
        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        graph.add_node(node)
        return values.Object(value)

class SumFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'sum'
        self.aggregateop = nodes.AggregateOpType.Sum

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        if len(args.inputs) == 1 and isinstance(args.get_value().inputs[0], values.ListValue):
            list_value = args.get_value().inputs[0]
            if list_value.internal_value:
                values_list = [ref.get_value() for ref in list_value.internal_value]
            else:
                values_list = []
        else:
            values_list = args.get_value().inputs
            node = nodes.NodeGenerate('List', values_list, line)
            list_value = values.ListValue([utils.try_get_obj(ref, self.name, line) for ref in args.inputs])
            node.set_outputs([list_value])
            graph.add_node(node)

        node = nodes.NodeAggregate(list_value, self.aggregateop, line)
        value = veval_multiary.veval(self.aggregateop, values_list, line)
        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        graph.add_node(node)
        return values.Object(value)

class PrintFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'print'
        self.args.add_arg('self', None)
        self.args.add_arg('v', None)

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        funcArgs = self.args.merge_inputs(inst, args)

        node = nodes.NodeCall(self, funcArgs, line)
        graph.add_node(node)

class ListFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'list'
        self.args.add_arg('value', values.Object(values.NoneValue()))

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        assert(inst is None)

        funcArgs = self.args.merge_inputs(inst, args)
        vargs = funcArgs.get_value().inputs
        value = values.ListValue()

        if isinstance(vargs[0], values.NoneValue):
            node = nodes.NodeGenerate('List', [], line)
            graph.add_node(node)
        else:
            node = nodes.NodeConvert('List', vargs[0], line)
            graph.add_node(node)

            if vargs[0].has_constant_value():
                refs = []
                for attr_or_ref in vargs[0].internal_value:
                    refs.append(utils.try_get_obj(attr_or_ref, 'list', utils.LineProperty()))

                value.internal_value = refs

        value.name = '@F.{}.{}'.format(line, self.name)
        node.set_outputs([value])
        return values.Object(value)

class VEvalContextFunction(functions.FunctionBase):
    def __init__(self, func):
        super().__init__()
        self.name = func.__name__
        self.args.analyze_args(func)

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        assert(inst is None)

        funcArgs = self.args.merge_inputs(inst, args)
        args = []
        for value in funcArgs.get_value().inputs:
            assert value.has_constant_value(), "Arguments for elichika.flags were non-constant at %s" % line
            args.append(value.internal_value)

        if option is not None:
            option.flags_cache.append((self.name, args))

        return values.Object(values.NoneValue())


class GetAttrFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'getattr'
        self.args.add_arg('object', None)
        self.args.add_arg('name', None)

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        func_args = self.args.merge_inputs(inst, args)
        name = func_args.get_value().get_value(key='name')
        obj = func_args.keywords['object']

        attr = obj.get_field().get_attribute(name.internal_value, graph.root_graph, False)

        # property(getter)
        if attr.has_obj() and isinstance(attr.get_obj().get_value(), values.FuncValue) and attr.get_obj().get_value().func.is_property:
            func_value = attr.get_obj().get_value()
            ret = func_value.func.vcall(func_value.module, graph, func_value.obj, functions.FunctionArgInput(), option, utils.LineProperty(line))
            return ret

        if attr.has_obj():
            return attr

        # if attr is not found
        gotten_obj = obj.try_get_and_store_obj(name.internal_value, graph.root_graph)
        if gotten_obj is not None:
            return obj.get_field().get_attribute(name.internal_value, graph.root_graph, False)

        return None


class HasAttrFunction(functions.FunctionBase):
    def __init__(self):
        super().__init__()
        self.name = 'hasattr'
        self.args.add_arg('obj', None)
        self.args.add_arg('name', None)

    def vcall(self, module: 'values.Field', graph: 'graphs.Graph', inst: 'values.Object', args: 'functions.FunctionArgInput',
              option: 'vevaluator.VEvalContext' = None, line=-1):
        func_args = self.args.merge_inputs(inst, args)
        name = func_args.get_value().get_value(key='name')
        obj = func_args.keywords['obj']

        attr = obj.get_field().get_attribute(name.internal_value, graph.root_graph, False)

        if attr.has_obj():
            return values.Object(values.BoolValue(True))

        # if attr is not found
        gotten_obj = obj.try_get_and_store_obj(name.internal_value, graph.root_graph)
        if gotten_obj is not None:
            return values.Object(values.BoolValue(True))

        return values.Object(values.BoolValue(False))