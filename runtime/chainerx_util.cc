#include "runtime/chainerx_util.h"

#include <cstring>
#include <limits>
#include <numeric>

#include <chainerx/array.h>
#include <chainerx/context.h>
#include <chainerx/kernels/connection.h>
#include <chainerx/kernels/creation.h>
#include <chainerx/native/native_backend.h>
#include <chainerx/native/native_device.h>
#include <chainerx/routines/connection.h>
#include <chainerx/routines/creation.h>
#include <chainerx/routines/linalg.h>
#include <chainerx/routines/logic.h>
#include <chainerx/routines/manipulation.h>

#ifdef CHAINER_COMPILER_ENABLE_CUDA
#include <chainerx/cuda/cuda_device.h>
#endif

#include <common/log.h>

namespace chainer_compiler {
namespace runtime {

chainerx::Array ShapeToArray(const chainerx::Shape& s) {
    chainerx::Shape shape{s.ndim()};
    return MakeHostArray(chainerx::Dtype::kInt64, shape, s.data());
}

chainerx::Shape ArrayToShape(const chainerx::Array& a) {
    // TODO(hamaji): Think again if we revive this.
    // WARN_ONCE("Shape info was not statically known.");

    // ONNX's document says "shape" of "Expand" op should be 1D tensor
    // while others are not explicitly specified. Here we will allow
    // scalar values as shapes to be aligned with numpy.
    if (a.ndim() == 0) return {int64_t(chainerx::AsScalar(a))};

    CHECK_EQ(a.ndim(), 1);
    chainerx::Shape shape;
    for (int i = 0; i < a.shape()[0]; ++i) {
        shape.push_back(int64_t(chainerx::AsScalar(a.At({i}))));
    }
    return shape;
}

namespace {

std::shared_ptr<void> MakeSharedPtrData(chainerx::Dtype dtype, chainerx::Shape shape, const void* src) {
    int64_t size = chainerx::GetItemSize(dtype) * shape.GetTotalSize();
    std::shared_ptr<void> data(new char[size], std::default_delete<char[]>());
    std::memcpy(data.get(), src, size);
    return data;
}

}  // namespace

chainerx::Array MakeArray(chainerx::Dtype dtype, chainerx::Shape shape, const void* src) {
    std::shared_ptr<void> data(MakeSharedPtrData(dtype, shape, src));
    chainerx::Array array(chainerx::FromContiguousHostData(shape, dtype, data));
    return array;
}

chainerx::Array MakeScalarArray(float f) {
    return MakeArray(chainerx::Dtype::kFloat32, {}, &f);
}

chainerx::Array MakeDtypeScalarArray(chainerx::Dtype dtype, chainerx::Scalar s) {
    return chainerx::Full({}, s, dtype);
}

chainerx::Array MakeHostArray(chainerx::Dtype dtype, chainerx::Shape shape, const void* src) {
    std::shared_ptr<void> data(MakeSharedPtrData(dtype, shape, src));
    chainerx::Array array(
            chainerx::FromData(shape, dtype, data, absl::nullopt /* strides */, 0 /* offset */, chainerx::GetNativeBackend().GetDevice(0)));
    return array;
}

std::vector<chainerx::Array> SplitByLengths(const chainerx::Array& input, int axis, const std::vector<int64_t>& split) {
    CHECK_EQ(std::accumulate(split.begin(), split.end(), 0), input.shape()[axis]);
    std::vector<chainerx::Array> results;
    std::vector<chainerx::ArrayIndex> indices(input.ndim(), chainerx::Slice());
    int start = 0;
    for (int len : split) {
        indices[axis] = chainerx::Slice(start, start + len);
        results.push_back(input.At(indices));
        start += len;
    }
    return results;
}

chainerx::Array PadSequence(const std::vector<chainerx::Array>& inputs, int64_t length, chainerx::Scalar padding) {
    // TODO(hamaji): Move this logic to ChainerX.
    CHECK_LT(0, inputs.size());
    int64_t max_length = 0;
    for (const chainerx::Array& input : inputs) {
        CHECK_EQ(input.dtype(), inputs[0].dtype());
        CHECK_EQ(input.ndim(), inputs[0].ndim());
        max_length = std::max(max_length, input.shape()[0]);
        for (int i = 1; i < input.ndim(); ++i) {
            CHECK_EQ(input.shape()[i], inputs[0].shape()[i]);
        }
    }
    if (length == 0) {
        length = max_length;
    } else {
        CHECK_GE(length, max_length) << "Pad overflow";
    }

    chainerx::Shape shape = inputs[0].shape();
    shape.insert(shape.begin(), inputs.size());
    shape[1] = length;
    chainerx::Array result = chainerx::Full(shape, padding, inputs[0].dtype(), inputs[0].device());
    std::vector<chainerx::ArrayIndex> indices(shape.ndim(), chainerx::Slice());
    for (size_t i = 0; i < inputs.size(); ++i) {
        const chainerx::Array& input = inputs[i];
        indices[0] = chainerx::ArrayIndex(i);
        indices[1] = chainerx::Slice(0, input.shape()[0]);
        BlitArray(input, result.At(indices));
    }
    return result;
}

namespace {

uint32_t xorshift() {
    static uint32_t y = 2463534242;
    y = y ^ (y << 13);
    y = y ^ (y >> 17);
    return y = y ^ (y << 15);
}

}  // namespace

chainerx::Array SlowRandom(chainerx::Shape shape) {
    int64_t size = shape.GetTotalSize();
    double denominator = 1.0 / std::pow(2.0, 32);
    std::vector<float> values(size);
    for (int64_t i = 0; i < size; ++i) {
        values[i] = xorshift() * denominator;
    }
    return MakeArray(chainerx::Dtype::kFloat32, shape, values.data());
}

chainerx::Array CastTo(const chainerx::Array& input, chainerx::Dtype dtype) {
    if (input.dtype() == dtype) return input;
    chainerx::Array output = input.AsType(dtype);
    // TODO(hamaji): Stop doing this ad-hoc device assignment.
    if (input.dtype() == chainerx::Dtype::kInt64 && output.dtype() != chainerx::Dtype::kInt64) {
        output = output.ToDevice(chainerx::GetDefaultDevice());
    } else if (input.dtype() != chainerx::Dtype::kInt64 && output.dtype() == chainerx::Dtype::kInt64) {
        output = output.ToDevice(chainerx::GetNativeBackend().GetDevice(0));
    }
    return output;
}

chainerx::OptionalAxes GetChainerXAxes(chainerx::StackVector<int64_t, chainerx::kMaxNdim> axes) {
    if (axes.empty()) return absl::nullopt;
    chainerx::Axes xc_axes{axes.begin(), axes.end()};
    return xc_axes;
}

bool IsNativeDevice(const chainerx::Device* device) {
    return dynamic_cast<const chainerx::native::NativeDevice*>(device) != nullptr;
}

bool IsCudaDevice(const chainerx::Device* device) {
#ifdef CHAINER_COMPILER_ENABLE_CUDA
    return dynamic_cast<const chainerx::cuda::CudaDevice*>(device) != nullptr;
#else
    return false;
#endif
}

namespace {

Int64StackVector ComplementStrideOrPad(const Int64StackVector& orig, const chainerx::Array& input, int default_value) {
    if (!orig.empty()) {
        return orig;
    }
    Int64StackVector filled;
    CHECK_LE(2, input.ndim()) << input.shape();
    for (int i = 0; i < input.ndim() - 2; ++i) {
        filled.push_back(default_value);
    }
    return filled;
}

}  // namespace

Int64StackVector ComplementStride(const Int64StackVector& strides, const chainerx::Array& input) {
    return ComplementStrideOrPad(strides, input, 1);
}

Int64StackVector ComplementPad(const Int64StackVector& pads, const chainerx::Array& input) {
    return ComplementStrideOrPad(pads, input, 0);
}

bool IsFloat(chainerx::Dtype dtype) {
    return chainerx::GetKind(dtype) == chainerx::DtypeKind::kFloat;
}

void BlitArray(const chainerx::Array& src, const chainerx::Array& dst) {
    src.device().backend().CallKernel<chainerx::CopyKernel>(src, dst);
}

chainerx::Array NumpyMatMul(const chainerx::Array& a, const chainerx::Array& b) {
    if (a.shape().size() <= 2) {
        return chainerx::Dot(a, b);
    }

    // TODO(take-cheeze): Better broadcasting compatibility with numpy
    if (chainerx::Shape(a.shape().begin(), a.shape().end() - 2) != chainerx::Shape(b.shape().begin(), b.shape().end() - 2)) {
        return chainerx::Dot(a, b);
    }

    const int64_t stack_len = std::accumulate(a.shape().begin(), a.shape().end() - 2, 1, std::multiplies<int64_t>());
    std::vector<chainerx::Array> stack(stack_len);
    chainerx::Array reshaped_a = a.Reshape({stack_len, *(a.shape().end() - 2), *(a.shape().end() - 1)});
    chainerx::Array reshaped_b = b.Reshape({stack_len, *(b.shape().end() - 2), *(b.shape().end() - 1)});
    for (int i = 0; i < stack_len; ++i) {
        stack[i] = Dot(reshaped_a.At({i}), reshaped_b.At({i}));
    }
    chainerx::Shape new_shape(a.shape().begin(), a.shape().end() - 2);
    new_shape.insert(new_shape.end(), stack.front().shape().begin(), stack.front().shape().end());
    return chainerx::Stack(stack).Reshape(new_shape);
}

chainerx::Array ApplyAsymmetricPad(const chainerx::Array& x, Int64StackVector* pads_ptr, float value, int64_t beg_dim) {
    Int64StackVector& pads = *pads_ptr;
    // Don't apply pad in symmetric pad
    if (pads.size() == (x.shape().size() - beg_dim)) {
        return x;
    }

    CHECK_EQ((x.ndim() - beg_dim) * 2, pads.size());
    const chainerx::Shape shape = x.shape();
    chainerx::Shape new_shape = x.shape();
    std::vector<chainerx::ArrayIndex> indices1, indices2;
    for (int i = 0; i < beg_dim; ++i) {
        indices1.push_back(chainerx::Slice(0, shape[i]));
        indices2.push_back(chainerx::Slice(0, shape[i]));
    }
    for (int i = beg_dim; i < shape.size(); ++i) {
        const int64_t pad_idx = i - beg_dim;
        const int64_t pad_beg = pads[pad_idx], pad_end = pads[pads.size() / 2 + pad_idx];
        new_shape[i] += pad_beg + pad_end;
        auto len = shape[i] + std::min<int64_t>(0, pad_beg) + std::min<int64_t>(0, pad_end);

        const auto start1 = std::max<int64_t>(-pad_beg, 0);
        const auto start2 = std::max<int64_t>(pad_beg, 0);
        const auto end1 = std::min(shape[i] + pad_end, shape[i]);
        const auto end2 = std::min(new_shape[i] - pad_end, new_shape[i]);

        CHECK_EQ(end1 - start1, len) << "Shape mis-match: " << shape[i] << " " << pad_beg << " " << pad_end << "      " << start1 << " "
                                     << end1 << " " << len;
        CHECK_EQ(end2 - start2, len) << "Shape mis-match: " << shape[i] << " " << pad_beg << " " << pad_end << "      " << start2 << " "
                                     << end2 << " " << len;

        indices1.push_back(chainerx::Slice(start1, end1));
        indices2.push_back(chainerx::Slice(start2, end2));
    }
    chainerx::Array result = chainerx::Full(new_shape, value, x.dtype(), x.device());
    BlitArray(x.At(indices1), result.At(indices2));

    // Clear applied pads
    pads.resize(x.shape().size() - beg_dim);
    std::fill(pads.begin(), pads.end(), 0);

    return result;
}

Int64StackVector CalculateAutoPad(
        const std::string& auto_pad,
        const chainerx::Array& x,
        const Int64StackVector& kernel_shape,
        const Int64StackVector& strides,
        const Int64StackVector& in_pads) {
    CHECK_EQ(kernel_shape.size(), in_pads.size());
    CHECK_EQ(strides.size(), in_pads.size());
    CHECK_EQ(x.shape().size(), in_pads.size() + 2);

    Int64StackVector pads = in_pads;
    Int64StackVector pads_end;
    pads_end.resize(in_pads.size());
    bool end_pad = false;
    if (!auto_pad.empty()) {
        CHECK_EQ(auto_pad, "SAME_UPPER");
        for (size_t i = 0; i < pads.size(); ++i) {
            const int64_t in_dim = x.shape()[2 + i];
            const int64_t stride = strides[i];
            const int64_t kernel = kernel_shape[i];

            int64_t legacy_target_size = (in_dim + stride - 1) / stride;
            int64_t pad_needed = (legacy_target_size - 1) * stride + kernel - in_dim;

            pads[i] = pad_needed / 2;
            pads_end[i] = pad_needed - pads[i];
            end_pad = end_pad || pads_end[i] > 0;
        }
    }

    if (end_pad) {
        pads.insert(pads.end(), pads_end.begin(), pads_end.end());
    }

    return pads;
}

chainerx::Array GroupedConv(
        const chainerx::Array& in_x,
        const chainerx::Array& w,
        const absl::optional<chainerx::Array>& b,
        const Int64StackVector& strides,
        const Int64StackVector& in_pads,
        int group,
        const std::string& auto_pad) {
    Int64StackVector pads = CalculateAutoPad(auto_pad, in_x, Int64StackVector(w.shape().begin() + 2, w.shape().end()), strides, in_pads);
    chainerx::Array x = ApplyAsymmetricPad(in_x, &pads);

    if (group == 1) {
        return chainerx::Conv(x, w, b, strides, pads);
    }

    std::vector<chainerx::Array> inputs = SplitByLengths(x, 1, std::vector<int64_t>(group, x.shape()[1] / group));
    std::vector<chainerx::Array> weights = SplitByLengths(w, 0, std::vector<int64_t>(group, w.shape()[0] / group));
    std::vector<chainerx::Array> biases;
    if (b.has_value()) {
        biases = SplitByLengths(*b, 0, std::vector<int64_t>(group, b->shape()[0] / group));
    }
    std::vector<chainerx::Array> outputs(group);
    for (int i = 0; i < group; ++i) {
        auto sub_bias = b.has_value() ? absl::optional<chainerx::Array>(biases[i]) : absl::nullopt;
        outputs[i] = chainerx::Conv(inputs[i], weights[i], sub_bias, strides, pads);
    }
    return chainerx::Concatenate(outputs, 1);
}

chainerx::Array GroupedConvTranspose(
        const chainerx::Array& x,
        const chainerx::Array& w,
        const absl::optional<chainerx::Array>& b,
        const Int64StackVector& strides,
        const Int64StackVector& pads,
        const Int64StackVector& output_shape,
        int group) {
    absl::optional<Int64StackVector> out_size = absl::nullopt;
    if (!output_shape.empty()) {
        out_size = output_shape;
    }
    if (group == 1) {
        return chainerx::ConvTranspose(x, w, b, strides, pads, out_size);
    }

    std::vector<chainerx::Array> inputs = SplitByLengths(x, 1, std::vector<int64_t>(group, x.shape()[1] / group));
    std::vector<chainerx::Array> weights = SplitByLengths(w, 0, std::vector<int64_t>(group, w.shape()[0] / group));
    std::vector<chainerx::Array> biases;
    if (b.has_value()) {
        biases = SplitByLengths(*b, 0, std::vector<int64_t>(group, b->shape()[0] / group));
    }
    std::vector<chainerx::Array> outputs(group);
    for (int i = 0; i < group; ++i) {
        auto sub_bias = b.has_value() ? absl::optional<chainerx::Array>(biases[i]) : absl::nullopt;
        outputs[i] = chainerx::ConvTranspose(inputs[i], weights[i], sub_bias, strides, pads, out_size);
    }
    return chainerx::Concatenate(outputs, 1);
}

chainerx::Array GroupedConvGradWeight(
        const chainerx::Array& w,
        const chainerx::Array& x,
        const chainerx::Array& gy,
        const Int64StackVector& strides,
        const Int64StackVector& pads,
        int group) {
    if (group == 1) {
        return x.device().backend().CallKernel<chainerx::ConvGradWeightKernel>(
                w.dtype(), w.shape(), x, gy, strides, pads, false /* cover_all */, absl::nullopt);
    }

    chainerx::Shape ws_shape = w.shape();
    ws_shape[0] /= group;
    std::vector<chainerx::Array> xs = SplitByLengths(x, 1, std::vector<int64_t>(group, x.shape()[1] / group));
    std::vector<chainerx::Array> gys = SplitByLengths(gy, 1, std::vector<int64_t>(group, gy.shape()[1] / group));
    std::vector<chainerx::Array> gws(group);
    for (int i = 0; i < group; ++i) {
        gws[i] = x.device().backend().CallKernel<chainerx::ConvGradWeightKernel>(
                w.dtype(), ws_shape, xs[i], gys[i], strides, pads, false /* cover_all */, absl::nullopt);
    }
    return chainerx::Concatenate(gws, 0);
}

// TODO(take-cheeze): Implement in ChainerX
chainerx::Array SlowRound(const chainerx::Array& x) {
    std::vector<double> result_data(x.GetTotalSize());
    chainerx::Array double_x = x.AsType(chainerx::Dtype::kFloat64);
    CHECK(IsNativeDevice(&x.device()));
    const double* x_ptr = reinterpret_cast<const double*>(RawStartPtr(double_x));
    for (size_t i = 0; i < result_data.size(); ++i) {
        result_data[i] = std::rint(x_ptr[i]);
    }

    chainerx::Array y = MakeArray(chainerx::Dtype::kFloat64, x.shape(), result_data.data());

    // Back to input(x) dtype
    return y.AsType(x.dtype());
}

void* RawStartPtr(const chainerx::Array& a) {
    CHECK(a.IsContiguous());
    return static_cast<char*>(a.raw_data()) + a.offset();
}

int ResolveAxis(const chainerx::Array& x, int axis) {
    const int64_t ax = axis < 0 ? axis + x.ndim() : axis;
    CHECK_GE(ax, 0);
    CHECK_LT(ax, x.ndim());
    return ax;
}

}  // namespace runtime
}  // namespace chainer_compiler
