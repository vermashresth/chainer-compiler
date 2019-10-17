#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <chainerx/shape.h>

#include "runtime/chxvm.pb.h"

namespace chainerx {
class Array;
}  // namespace chainerx

namespace chainer_compiler {
namespace runtime {

class ChromeTracingEmitter;
class ChxVMOp;
class ChxVMState;
class ChxVMVar;

typedef std::map<std::string, std::shared_ptr<ChxVMVar>> InOuts;

typedef std::function<std::vector<chainerx::Array>(std::vector<chainerx::Array>)> CustomOpFunc;

struct ChxVMOptions {
public:
    ChxVMOptions();

    // trace_level=0: No trace
    // trace_level=1: Dump shapes
    // trace_level=2: Dump values
    int trace_level{0};

    std::vector<bool> verbose_ops;

    bool is_training{false};

    bool check_types{false};

    bool check_nans{false};

    bool check_infs{false};

    bool catch_exception{true};

    // dump_memory_usage=0: No dump
    // dump_memory_usage=1: Dump peak memory usage only
    // dump_memory_usage=2: Dump intermediate memory usage
    int dump_memory_usage{0};
    int64_t base_memory_usage{-1};

    ChromeTracingEmitter* chrome_tracing{nullptr};

    std::string dump_outputs_dir;

    std::map<std::string, CustomOpFunc> custom_op_funcs;
};

struct ChxVMInputDesc;

class ChxVM {
public:
    ChxVM(const ChxVMProgramProto& program, bool should_init = true);
    ~ChxVM();

    void Init();

    std::unique_ptr<ChxVMState> Prepare(const InOuts& program_inputs, const ChxVMOptions& options);
    InOuts Run(const InOuts& program_inputs, const ChxVMOptions& options);
    void Run(ChxVMState* state);

    int num_variables() const {
        return num_variables_;
    }

private:
    ChxVM(const ChxVM&) = delete;
    ChxVM& operator=(const ChxVM&) = delete;

    std::vector<std::unique_ptr<ChxVMOp>> program_;
    std::vector<std::unique_ptr<ChxVMInputDesc>> input_descs_;
    int num_variables_;
};

}  // namespace runtime
}  // namespace chainer_compiler
