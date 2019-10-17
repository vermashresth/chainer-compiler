#include <map>

#include <chainerx/array.h>
#include <chainerx/routines/creation.h>
#include <chainerx/shape.h>

#if CHAINER_COMPILER_ENABLE_NVRTC
#include <chainerx/cuda/cuda_device.h>
#include <cuda.h>
#include <nvrtc.h>
#endif

#include <common/log.h>
#include <common/strutil.h>
#include <runtime/chainerx_util.h>
#include <runtime/gen_chxvm_ops.h>

namespace chainer_compiler {
namespace runtime {

#if CHAINER_COMPILER_ENABLE_NVRTC

namespace {

void check_nvrtc(nvrtcResult status, const char* msg, int lineno) {
    CHECK_EQ(NVRTC_SUCCESS, status) << "NVRTC error: " << nvrtcGetErrorString(status) << " at line " << lineno;
}

#define CHECK_NVRTC(expr) check_nvrtc(expr, #expr, __LINE__)

void check_cuda(CUresult status, const char* msg, int lineno) {
    if (status != CUDA_SUCCESS) {
        const char* err = nullptr;
        cuGetErrorString(status, &err);
        CHECK_EQ(CUDA_SUCCESS, status) << "CUDA: " << err << " at line " << lineno;
    }
}

#define CHECK_CUDA(expr) check_cuda(expr, #expr, __LINE__)

char* Compile(const std::string& name, const std::string& code) {
    static std::map<const std::string, char*> cache;
    auto found = cache.find(code);
    if (found != cache.end()) return found->second;

    nvrtcProgram prog;
    CHECK_NVRTC(nvrtcCreateProgram(&prog, code.c_str(), (name + ".cu").c_str(), 0, nullptr, nullptr));

    const char* kOpts[] = {
            "--gpu-architecture=compute_50",
    };
    nvrtcResult result = nvrtcCompileProgram(prog, 1, kOpts);
    // Obtain compilation log from the program.
    size_t log_size;
    CHECK_NVRTC(nvrtcGetProgramLogSize(prog, &log_size));
    char* log = new char[log_size];
    CHECK_NVRTC(nvrtcGetProgramLog(prog, log));
    CHECK_EQ(result, NVRTC_SUCCESS) << code << "\nlog:\n" << log;
    // Obtain PTX from the program.
    size_t ptxSize;
    CHECK_NVRTC(nvrtcGetPTXSize(prog, &ptxSize));
    char* ptx = new char[ptxSize];
    CHECK_NVRTC(nvrtcGetPTX(prog, ptx));
    delete[] log;

    CHECK(cache.emplace(code, ptx).second);
    return ptx;
}

CUfunction CompileAndLoad(const std::string& name, const std::string& code) {
    static std::map<const std::string, CUfunction> cache;
    auto found = cache.find(code);
    if (found != cache.end()) return found->second;

    char* ptx = Compile(name, code);

    CUmodule cu_module;
    CUfunction cu_kernel;
    CHECK_CUDA(cuModuleLoadDataEx(&cu_module, ptx, 0, 0, 0));
    CHECK_CUDA(cuModuleGetFunction(&cu_kernel, cu_module, name.c_str()));

    CHECK(cache.emplace(code, cu_kernel).second);
    return cu_kernel;
}

}  // namespace

#endif

std::vector<chainerx::Array> ElementWiseNvrtcOp::RunImpl(
        chainer_compiler::runtime::ChxVMState* st, const std::vector<chainerx::Array>& orig_inputs) {
#if CHAINER_COMPILER_ENABLE_NVRTC
    CHECK(!inputs.empty());
    const std::string& name = StrCat("fusion", fusion_id);
    auto& device = dynamic_cast<chainerx::cuda::CudaDevice&>(orig_inputs[0].device());

    // Validate inputs.
    chainerx::Dtype dtype = orig_inputs[0].dtype();
    chainerx::Shape shape = orig_inputs[0].shape();
    std::vector<chainerx::Array> inputs;
    for (chainerx::Array input : orig_inputs) {
        CHECK_EQ(dtype, input.dtype());
        shape = chainerx::internal::BroadcastShapes(shape, input.shape());
    }

    for (chainerx::Array input : orig_inputs) {
        if (shape != input.shape()) {
            // TODO(hamaji): Generate code which works without broadcast.
            input = input.BroadcastTo(shape);
        }
        input = chainerx::AsContiguous(input);
        inputs.push_back(input);
    }

    std::vector<chainerx::Array> outputs;
    for (int i = 0; i < num_outputs; ++i) {
        outputs.push_back(chainerx::Empty(shape, dtype, device));
    }

#if 0
    std::cerr << "\nname of kernel: " << name << std::endl;
    std::cerr << "# of inputs: " << inputs.size() << std::endl;
    std::cerr << "# of outputs: " << outputs.size() << std::endl;
    std::cerr << "code:\n" << code << std::endl;
#endif
    CUfunction cu_kernel = CompileAndLoad(name, code);

    size_t size = inputs.front().GetTotalSize();
    CHECK_GT(1 << 31, size);
    const size_t block_max_size = 128;
    const size_t grid_x = (size + block_max_size - 1) / block_max_size;
    const size_t block_x = std::min(block_max_size, size);
    std::vector<void*> ptrs;
    for (chainerx::Array& input : inputs) {
        ptrs.push_back(RawStartPtr(input));
    }
    for (chainerx::Array& output : outputs) {
        ptrs.push_back(RawStartPtr(output));
    }
    std::vector<void*> args = {&size};
    for (void*& p : ptrs) args.push_back(&p);

    CHECK_CUDA(cuLaunchKernel(
            cu_kernel,
            grid_x,
            1,
            1,  // grid dim
            block_x,
            1,
            1,  // block dim
            0,
            NULL,  // shared mem and stream
            args.data(),  // arguments
            0));

    return outputs;

#else
    CHECK(false) << "Set -DCHAINER_COMPILER_ENABLE_NVRTC=ON: code=" << code;
#endif
}

}  // namespace runtime
}  // namespace chainer_compiler
