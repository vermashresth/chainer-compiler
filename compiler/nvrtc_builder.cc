#include "compiler/nvrtc_builder.h"

#include <algorithm>
#include <iterator>
#include <locale>
#include <map>
#include <queue>
#include <set>
#include <sstream>

#include <common/log.h>
#include <compiler/code_emitter.h>
#include <compiler/node.h>
#include <compiler/tensor.h>
#include <compiler/type.h>
#include <compiler/value.h>

namespace chainer_compiler {

namespace {

std::string CleanseIdent(const std::string& s, const char* prefix = "v_") {
    std::locale loc;
    std::string o = prefix;
    for (char c : s) {
        if (std::isalnum(c, loc)) {
            o += c;
        } else {
            o += '_';
        }
    }
    return o;
}

void EmitNode(const Node* node, CodeEmitter* ce) {
    std::vector<std::string> ins;
    std::vector<std::string> outs;
    for (Value* value : node->inputs()) ins.push_back(CleanseIdent(value->name()));
    for (Value* value : node->outputs()) outs.push_back(CleanseIdent(value->name()));

    auto out1 = [&outs, node, ce](const std::string& rhs) {
        CHECK_EQ(1UL, outs.size());
        *ce << "const T " << outs[0] << " = " << rhs << ";  // " << node->op_type() << "\n";
    };

    auto binary = [&ins, out1](char op) {
        CHECK_EQ(2UL, ins.size());
        out1(ins[0] + ' ' + op + ' ' + ins[1]);
    };

    switch (node->op_type()) {
        case Node::kIdentity:
            out1(ins[0]);
            break;

        case Node::kTanh:
            out1("tanh(" + ins[0] + ")");
            break;

        case Node::kExp:
            out1("exp(" + ins[0] + ")");
            break;

        case Node::kSigmoid:
            out1("sigmoid(" + ins[0] + ")");
            break;

        case Node::kAdd:
            binary('+');
            break;

        case Node::kSub:
            binary('-');
            break;

        case Node::kMul:
            binary('*');
            break;

        case Node::kDiv:
            binary('/');
            break;

        default:
            CHECK(false) << "Cannot build NVRTC program for: " << node->ToString();
    }
}

}  // namespace

void BuildNvrtcProgram(
        const std::vector<Node*>& nodes, int id, const std::vector<Value*>& inputs, const std::vector<Value*>& outputs, std::string* prog) {
    std::set<Node::OpType> seen_ops;
    for (Node* node : nodes) {
        seen_ops.insert(node->op_type());
    }

    // TODO(hamaji): Currently, we assume unknown dtype is float32.
    Dtype dtype = Dtype::kUnknown;
    for (Node* node : nodes) {
        for (Value* value : node->inputs()) {
            Dtype dt = value->type().dtype();
            if (dt == Dtype::kUnknown) {
                continue;
            }
            if (dtype != Dtype::kUnknown) {
                CHECK_EQ(dtype, dt);
            }
            dtype = dt;
        }
    }
    if (dtype == Dtype::kUnknown) {
        dtype = Dtype::kFloat32;
    }

    std::ostringstream oss;
    CodeEmitter ce(oss);
    switch (dtype) {
        case Dtype::kFloat16:
            ce << "typedef half T;\n";
            break;
        case Dtype::kFloat32:
            ce << "typedef float T;\n";
            break;
        case Dtype::kFloat64:
            ce << "typedef double T;\n";
            break;
        default:
            CHECK(false) << "Unknown dtype: " << dtype;
    }

    if (seen_ops.count(Node::kSigmoid)) {
        ce << "__device__ T sigmoid(T x) {\n";
        ce << "const T half = 0.5;\n";
        ce << "return tanh(x * half) * half + half;\n";
        ce << "}\n";
    }

    ce << "extern \"C\" __global__\n";
    ce << "void fusion" << id << "(size_t n";
    for (Value* value : inputs) {
        ce << ", T* " << CleanseIdent(value->name(), "i_");
    }
    for (Value* value : outputs) {
        ce << ", T* " << CleanseIdent(value->name(), "o_");
    }
    ce << ") {\n";
    ce << "size_t tid = blockIdx.x * blockDim.x + threadIdx.x;\n";
    ce << "if (tid >= n) return;\n";
    for (Value* value : inputs) {
        ce << "const T " << CleanseIdent(value->name()) << " = " << CleanseIdent(value->name(), "i_") << "[tid];  // input\n";
    }

    std::map<Node*, int> input_counts;
    for (Node* node : nodes) {
        CHECK(input_counts.emplace(node, node->GetNumActualInputs()).second);
    }

    std::queue<Value*> q;
    for (Value* value : inputs) {
        q.push(value);
    }

    for (Node* node : nodes) {
        if (node->op_type() != Node::kConstant) continue;
        q.push(node->output(0));
        Tensor* t = node->tensor_value().get();
        CHECK_EQ(1, t->NumElements()) << t->dtype();
        double value;
        switch (t->dtype()) {
            case Dtype::kFloat16:
                value = static_cast<double>(t->Get<chainerx::Float16>(0));
                break;
            case Dtype::kFloat32:
                value = t->Get<float>(0);
                break;
            case Dtype::kFloat64:
                value = t->Get<double>(0);
                break;
            default:
                CHECK(false) << t->dtype();
        }
        ce << "const T " << CleanseIdent(node->output(0)->name()) << " = " << value << ";  // Constant\n";
    }

    while (!q.empty()) {
        Value* value = q.front();
        q.pop();

        for (Node* node : value->users()) {
            auto found = input_counts.find(node);
            if (found == input_counts.end()) continue;
            if (--found->second != 0) continue;
            EmitNode(node, &ce);
            for (Value* value : node->outputs()) q.push(value);
        }
    }

    for (Value* value : outputs) {
        ce << CleanseIdent(value->name(), "o_") << "[tid] = " << CleanseIdent(value->name()) << ";  // output\n";
    }

    ce << "}\n";

    *prog = oss.str();
}

}  // namespace chainer_compiler
