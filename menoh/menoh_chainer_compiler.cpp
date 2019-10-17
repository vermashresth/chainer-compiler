#include <menoh/menoh.h>

#include <iostream>

#include <absl/types/optional.h>

#include <nlohmann/json.hpp>

#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl.h>

#include <chainerx/array.h>
#include <chainerx/backprop_mode.h>
#include <chainerx/routines/creation.h>

#include <common/log.h>
#include <common/protoutil.h>
#include <compiler/chxvm/emitter.h>
#include <compiler/custom_onnx_ops.h>
#include <compiler/flags.h>
#include <compiler/graph.h>
#include <compiler/model.h>
#include <compiler/onnx.h>
#include <compiler/passes.h>
#include <compiler/util.h>
#include <runtime/chainerx_util.h>
#include <runtime/chxvm.h>
#include <runtime/chxvm.pb.h>
#include <runtime/chxvm_var.h>
#include <tools/util.h>

#include <menoh/menoh_chainer_compiler_util.hpp>

namespace menoh_impl {
using fixed_array = std::array<char, MENOH_ERROR_MESSAGE_MAX_LENGTH>;
fixed_array& get_error_message_singleton() noexcept {
    thread_local fixed_array message = {'\0'};
    return message;
}

void set_last_error_message(const char* message) noexcept {
    auto& arr = get_error_message_singleton();
    auto message_size = std::char_traits<char>::length(message) + 1;  // +1 for null char
    if (arr.size() < message_size) {
        const char* prefix =
                "An error occured, and its log message is longer than prepared. "
                "To view all message, please extend "
                "\"menoh_error_message_max_length\" (all capitals) macro: ";
        auto cont = std::copy(prefix, prefix + std::char_traits<char>::length(prefix), arr.begin());
        std::copy(message, message + (static_cast<size_t>(arr.end() - cont) - 1), cont);

    } else {
        std::copy(message, message + message_size, arr.data());
    }
}
}  // namespace menoh_impl

#undef MENOH_ERROR_MESSAGE_MAX_LENGTH

const char* menoh_get_last_error_message() {
    return menoh_impl::get_error_message_singleton().data();
}

template <typename Func>
menoh_error_code check_error(Func func) {
    try {
        menoh_error_code ec = func();
        if (ec) {
            return ec;
        }
    } catch (std::exception const& e) {
        menoh_impl::set_last_error_message(e.what());
        return menoh_error_code_std_error;  //
    } catch (...) {
        menoh_impl::set_last_error_message("");
        return menoh_error_code_unknown_error;  //
    }
    return menoh_error_code_success;
}

#undef MENOH_ERROR_MESSAGE_MAX_LENGTH

/*
 * dtype
 */

namespace menoh_impl {
template <menoh_dtype_constant>
struct dtype_to_type {};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_float16> {
    using type = int16_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_float32> {  // including dtype_t::float_
    using type = float;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_float64> {
    using type = double;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_int8> {
    using type = int8_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_int16> {
    using type = int16_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_int32> {
    using type = int32_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_int64> {
    using type = int64_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_uint8> {
    using type = uint8_t;
};

template <>
struct dtype_to_type<menoh_dtype_constant::menoh_dtype_bool> {
    using type = bool;
};

template <menoh_dtype_constant d>
using dtype_to_type_t = typename dtype_to_type<d>::type;
template <menoh_dtype_constant d>
constexpr int size_in_bytes = sizeof(dtype_to_type_t<d>);
}  // namespace menoh_impl

menoh_error_code MENOH_API menoh_dtype_size(menoh_dtype dtype, int64_t* dst_size) {
    switch (dtype) {
#define MENOH_DTYPE_SIZE_CASE(dtype)                                                     \
    case dtype:                                                                          \
        *dst_size = menoh_impl::size_in_bytes<static_cast<menoh_dtype_constant>(dtype)>; \
        break;
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_float)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_float16)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_float64)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_int8)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_int16)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_int32)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_int64)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_uint8)
        MENOH_DTYPE_SIZE_CASE(menoh_dtype_bool)
#undef MENOH_DTYPE_SIZE_CASE
        default:
            std::string msg("unknown dtype: " + std::to_string(dtype));
            menoh_impl::set_last_error_message(msg.c_str());
            return menoh_error_code_invalid_dtype;
    }
    return menoh_error_code_success;
}

onnx::TensorProto::DataType menoh_dtype_to_xtensor_dtype(menoh_dtype mdtype) {
    if (mdtype == menoh_dtype_undefined) {
        return onnx::TensorProto::UNDEFINED;
    } else if (mdtype == menoh_dtype_float) {
        return onnx::TensorProto::FLOAT;
    } else if (mdtype == menoh_dtype_float16) {
        return onnx::TensorProto::FLOAT16;
    } else if (mdtype == menoh_dtype_float64) {
        return onnx::TensorProto::DOUBLE;
    } else if (mdtype == menoh_dtype_int8) {
        return onnx::TensorProto::INT8;
    } else if (mdtype == menoh_dtype_int16) {
        return onnx::TensorProto::INT16;
    } else if (mdtype == menoh_dtype_int32) {
        return onnx::TensorProto::INT32;
    } else if (mdtype == menoh_dtype_int64) {
        return onnx::TensorProto::INT64;
    } else if (mdtype == menoh_dtype_uint8) {
        return onnx::TensorProto::UINT8;
    } else if (mdtype == menoh_dtype_bool) {
        return onnx::TensorProto::BOOL;
    } else {
        CHECK(false) << "Not Implemeneted menoh_dtype: " << mdtype;
    }
    return onnx::TensorProto::UNDEFINED;
}

chainerx::Dtype menoh_dtype_to_chx_dtype(menoh_dtype mdtype) {
    return static_cast<chainerx::Dtype>(static_cast<int>(menoh_dtype_to_cc_dtype(mdtype)));
}

/*
 * model_data
 */
struct menoh_model_data {
    onnx::GraphProto xgraph;
    // std::shared_ptr<chainer_compiler::Graph> graph;
};

void menoh_delete_model_data(menoh_model_data_handle model_data) {
    delete model_data;
}

namespace {

onnx::ModelProto load_model_proto(const uint8_t* onnx_data, int64_t size) {
    onnx::ModelProto proto;
    ::google::protobuf::io::ArrayInputStream ais(onnx_data, size);
    ::google::protobuf::io::CodedInputStream cis(&ais);
    cis.SetTotalBytesLimit(std::numeric_limits<int>::max(), std::numeric_limits<int>::max());
    CHECK(proto.ParseFromCodedStream(&cis)) << "failed to parse from memory";
    return proto;
}

}  // namespace

menoh_error_code menoh_make_model_data_from_onnx_data_on_memory(
        const uint8_t* onnx_data, int64_t size, menoh_model_data_handle* dst_handle) {
    return check_error([&]() {
        *dst_handle = std::make_unique<menoh_model_data>(menoh_model_data{load_model_proto(onnx_data, size).graph()}).release();
        return menoh_error_code_success;
    });
}

menoh_error_code menoh_make_model_data_from_onnx(const char* onnx_filename, menoh_model_data_handle* dst_handle) {
    return check_error([&]() {
        *dst_handle =
                std::make_unique<menoh_model_data>(menoh_model_data{LoadLargeProto<onnx::ModelProto>(onnx_filename).graph()}).release();
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_input_name_list_size(menoh_model_data_handle model_data, int64_t* dst_size) {
    return check_error([&]() {
        *dst_size = model_data->xgraph.input().size();
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_input_name_size(menoh_model_data_handle model_data, int64_t index, int64_t* dst_size) {
    return check_error([&]() {
        *dst_size = model_data->xgraph.input(index).name().size();
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_input_name(menoh_model_data_handle model_data, int64_t index, char* dst_name) {
    return check_error([&]() {
        auto const& name = model_data->xgraph.input(index).name();
        std::copy(name.c_str(), name.c_str() + name.size() + 1, dst_name);
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_output_name_list_size(menoh_model_data_handle model_data, int64_t* dst_size) {
    return check_error([&]() {
        *dst_size = model_data->xgraph.output().size();
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_output_name_size(menoh_model_data_handle model_data, int64_t index, int64_t* dst_size) {
    return check_error([&]() {
        *dst_size = model_data->xgraph.output(index).name().size();
        return menoh_error_code_success;
    });
}

menoh_error_code MENOH_API menoh_model_data_get_output_name(menoh_model_data_handle model_data, int64_t index, char* dst_name) {
    return check_error([&]() {
        auto const& name = model_data->xgraph.output(index).name();
        std::copy(name.c_str(), name.c_str() + name.size() + 1, dst_name);
        return menoh_error_code_success;
    });
}

/*
 * variable_profile_table_builder
 */

namespace menoh_impl {

class array_profile {
public:
    array_profile() = default;

    array_profile(menoh_dtype dtype, std::vector<int64_t> const& dims) : dtype_(dtype), dims_(dims) {
        for (int64_t d : dims) {
            // TODO(take-cheeze): Some ONNX tensor's size is 0
            CHECK_LE(0, d);
        }
    }

    menoh_dtype dtype() const {
        return dtype_;
    }
    auto const& dims() const {
        return dims_;
    }

private:
    menoh_dtype dtype_ = static_cast<int64_t>(menoh_dtype_constant::menoh_dtype_undefined);
    std::vector<int64_t> dims_;
};

bool has_dynamic_shape(array_profile const& a) {
    return a.dims().empty();
}

size_t total_size(std::vector<int64_t> const& dims) {
    return std::accumulate(dims.begin(), dims.end(), 1, std::multiplies<int64_t>());
}

size_t total_size_in_bytes(menoh_dtype dtype, std::vector<int64_t> const& dims) {
    int64_t dtype_size;
    menoh_dtype_size(dtype, &dtype_size);
    CHECK_LT(0, dtype_size);
    return dtype_size * total_size(dims);
}

size_t total_size_in_bytes(array_profile const& p) {
    return total_size_in_bytes(p.dtype(), p.dims());
}
}  // namespace menoh_impl
struct menoh_variable_profile_table_builder {
    std::unordered_map<std::string, menoh_impl::array_profile> input_profiles;
    std::vector<std::string> required_output_names;
};

void menoh_delete_variable_profile_table_builder(menoh_variable_profile_table_builder_handle builder) {
    delete builder;
}

menoh_error_code menoh_make_variable_profile_table_builder(menoh_variable_profile_table_builder_handle* dst_handle) {
    return check_error([&]() {
        *dst_handle = std::make_unique<menoh_variable_profile_table_builder>().release();
        return menoh_error_code_success;
    });
}

menoh_error_code menoh_variable_profile_table_builder_add_input_profile(
        menoh_variable_profile_table_builder_handle builder, const char* name, menoh_dtype dtype, int64_t dims_size, const int64_t* dims) {
    return check_error([&]() {
        auto found = builder->input_profiles.find(name);
        if (found != builder->input_profiles.end()) {
            auto message = std::string("menoh same named variable already exist: ") + name;
            menoh_impl::set_last_error_message(message.c_str());
            return menoh_error_code_same_named_variable_already_exist;
        }
        builder->input_profiles.emplace(std::string(name), menoh_impl::array_profile(dtype, std::vector<int64_t>(dims, dims + dims_size)));
        return menoh_error_code_success;
    });
}

menoh_error_code menoh_variable_profile_table_builder_add_output_name(
        menoh_variable_profile_table_builder_handle builder, const char* name) {
    return check_error([&]() {
        auto found = std::find(builder->required_output_names.begin(), builder->required_output_names.end(), std::string(name));
        if (found != builder->required_output_names.end()) {
            auto message = std::string("menoh same named variable already exist: ") + name;
            menoh_impl::set_last_error_message(message.c_str());
            return menoh_error_code_same_named_variable_already_exist;
        }
        builder->required_output_names.emplace_back(name);
        return menoh_error_code_success;
    });
}

struct menoh_variable_profile_table {
    std::shared_ptr<const onnx::GraphProto> xgraph;
    std::unordered_map<std::string, menoh_impl::array_profile> input_profiles;
    std::unordered_map<std::string, menoh_impl::array_profile> output_profiles;
    bool is_dynamic_model{false};
};

void menoh_delete_variable_profile_table(menoh_variable_profile_table_handle variable_profile_table) {
    delete variable_profile_table;
}

menoh_error_code menoh_build_variable_profile_table(
        const menoh_variable_profile_table_builder_handle builder,
        const menoh_model_data_handle model_data,
        menoh_variable_profile_table_handle* dst_handle) {
    return check_error([&]() {
        chainerx::Context ctx;
        chainerx::ContextScope ctx_scope(ctx);

        // Construct graph without initializer
        std::unique_ptr<chainer_compiler::Graph> graph;
        {
            onnx::GraphProto xgraph;
            xgraph.set_doc_string(model_data->xgraph.doc_string());
            xgraph.mutable_node()->CopyFrom(model_data->xgraph.node());
            // xgraph.mutable_initializer()->CopyFrom(model_data->xgraph.initializer()); // Skip
            xgraph.mutable_input()->CopyFrom(model_data->xgraph.input());
            xgraph.mutable_output()->CopyFrom(model_data->xgraph.output());
            xgraph.mutable_value_info()->CopyFrom(model_data->xgraph.value_info());
            graph = std::make_unique<chainer_compiler::Graph>(xgraph);
        }

        // Check output is contained in the model
        std::vector<chainer_compiler::Value*> required_output_values;
        for (std::string required_output_name : builder->required_output_names) {
            auto found = std::find_if(graph->all_values().begin(), graph->all_values().end(), [&required_output_name](auto const& v) {
                return v->name() == required_output_name;
            });
            if (found == graph->all_values().end()) {
                auto message = std::string("required output is not contained in the model: ") + required_output_name;
                menoh_impl::set_last_error_message(message.c_str());
                return menoh_error_code_output_not_found_error;
            }
            required_output_values.push_back(found->get());
        }

        // Extract necessary values
        std::set<chainer_compiler::Value*> necessary_values_set = graph->GetNecessaryValues(required_output_values);
        std::vector<chainer_compiler::Value*> necessary_values(necessary_values_set.begin(), necessary_values_set.end());
        auto end_iter = std::remove_if(necessary_values.begin(), necessary_values.end(), [builder](chainer_compiler::Value* v) {
            return std::find_if(builder->input_profiles.begin(), builder->input_profiles.end(), [v](auto const& p) {
                       return p.first == v->name();
                   }) == builder->input_profiles.end();
        });
        std::vector<chainer_compiler::Value*> necessary_input_values(necessary_values.begin(), end_iter);

        // Modify xgraph
        onnx::GraphProto xgraph;
        graph->ToONNX(&xgraph);
        for (chainer_compiler::Value* input_value : necessary_input_values) {
            auto found = std::find_if(builder->input_profiles.begin(), builder->input_profiles.end(), [input_value](auto const& p) {
                return p.first == input_value->name();
            });
            auto const& name = found->first;
            auto const& profile = found->second;
            auto value_info = std::find_if(xgraph.mutable_input()->begin(), xgraph.mutable_input()->end(), [&name](auto const& input) {
                return input.name() == name;
            });
            auto type = std::make_unique<onnx::TypeProto>();
            auto tensor_type = std::make_unique<onnx::TypeProto_Tensor>();
            auto shape = std::make_unique<onnx::TensorShapeProto>();
            for (size_t i = 0; i < profile.dims().size(); ++i) {
                shape->add_dim();
                shape->mutable_dim(i)->set_dim_value(profile.dims()[i]);
            }
            tensor_type->set_allocated_shape(shape.release());
            tensor_type->set_elem_type(menoh_dtype_to_xtensor_dtype(profile.dtype()));
            type->set_allocated_tensor_type(tensor_type.release());
            value_info->set_allocated_type(type.release());
        }

        xgraph.clear_output();
        for (std::string const& output_name : builder->required_output_names) {
            auto* value_info = xgraph.add_output();
            value_info->set_name(output_name);
            auto type = std::make_unique<onnx::TypeProto>();
            auto tensor_type = std::make_unique<onnx::TypeProto_Tensor>();
            type->set_allocated_tensor_type(tensor_type.release());
            value_info->set_allocated_type(type.release());
        }

        // Remove value_info element contained in output
        auto value_info_end_iter =
                std::remove_if(xgraph.mutable_value_info()->begin(), xgraph.mutable_value_info()->end(), [builder](auto const& value_info) {
                    return std::find_if(
                                   builder->required_output_names.begin(),
                                   builder->required_output_names.end(),
                                   [&value_info](std::string const& name) { return name == value_info.name(); }) !=
                           builder->required_output_names.end();
                });
        xgraph.mutable_value_info()->erase(value_info_end_iter, xgraph.mutable_value_info()->end());

        // InferShape
        graph = std::make_unique<chainer_compiler::Graph>(xgraph);  // Reset graph
        graph->InferShapes();

        std::unordered_map<std::string, menoh_impl::array_profile> output_profiles;
        bool is_dynamic = false;
        for (chainer_compiler::Value* value : graph->output_values()) {
            if (!value->type().HasKnownShape()) {
                is_dynamic = true;
                continue;
            }
            output_profiles.emplace(
                    value->name(), menoh_impl::array_profile(cc_dtype_to_menoh_dtype(value->type().dtype()), value->type().dims()));
        }
        {
            auto xgraph_ptr = std::make_unique<onnx::GraphProto>();
            graph->ToONNX(xgraph_ptr.get());
            *dst_handle = std::make_unique<menoh_variable_profile_table>(
                                  menoh_variable_profile_table{
                                          std::move(xgraph_ptr), builder->input_profiles, std::move(output_profiles), is_dynamic})
                                  .release();
        }
        return menoh_error_code_success;
    });
}

namespace impl {
template <typename F>
menoh_error_code menoh_variable_profile_table_get_variable_attribute(
        const menoh_variable_profile_table_handle variable_profile_table, const char* name, F f) {
    return check_error([&]() {
        auto outiter = variable_profile_table->output_profiles.find(name);
        if (outiter != variable_profile_table->output_profiles.end()) {
            f(outiter->second);
        } else {
            auto initer = variable_profile_table->input_profiles.find(name);
            if (initer != variable_profile_table->input_profiles.end()) {
                f(initer->second);
            } else {
                auto message = std::string("menoh variable not found: ") + name;
                menoh_impl::set_last_error_message(message.c_str());
                return menoh_error_code_variable_not_found;
            }
        }
        return menoh_error_code_success;
    });
}
}  // namespace impl

menoh_error_code menoh_variable_profile_table_get_dtype(
        const menoh_variable_profile_table_handle variable_profile_table, const char* name, menoh_dtype* dst_dtype) {
    CHECK(!variable_profile_table->is_dynamic_model) << "cannot get dtype from dynamic graph";
    return impl::menoh_variable_profile_table_get_variable_attribute(
            variable_profile_table, name, [&](auto const& profile) { *dst_dtype = static_cast<menoh_dtype>(profile.dtype()); });
}
menoh_error_code menoh_variable_profile_table_get_dims_size(
        const menoh_variable_profile_table_handle variable_profile_table, const char* name, int64_t* dst_size) {
    CHECK(!variable_profile_table->is_dynamic_model) << "cannot get shape from dynamic graph";
    return impl::menoh_variable_profile_table_get_variable_attribute(
            variable_profile_table, name, [&](auto const& profile) { *dst_size = static_cast<int64_t>(profile.dims().size()); });
}
menoh_error_code menoh_variable_profile_table_get_dims_at(
        const menoh_variable_profile_table_handle variable_profile_table, const char* name, int64_t index, int64_t* dst_size) {
    CHECK(!variable_profile_table->is_dynamic_model) << "cannot get shape from dynamic graph";
    return impl::menoh_variable_profile_table_get_variable_attribute(
            variable_profile_table, name, [&](auto const& profile) { *dst_size = profile.dims().at(index); });
}

menoh_error_code menoh_variable_profile_table_get_dims(
        const menoh_variable_profile_table_handle variable_profile_table, const char* name, int64_t* dst_size, const int64_t** dims) {
    CHECK(!variable_profile_table->is_dynamic_model) << "cannot get shape from dynamic graph";
    return impl::menoh_variable_profile_table_get_variable_attribute(variable_profile_table, name, [&](auto const& profile) {
        *dst_size = profile.dims().size();
        *dims = profile.dims().data();
    });
}

/*
 * model builder
 */
struct menoh_model_builder {
    std::shared_ptr<const onnx::GraphProto> xgraph;
    std::unordered_map<std::string, menoh_impl::array_profile> input_profile_table;
    std::unordered_map<std::string, menoh_impl::array_profile> output_profile_table;
    std::unordered_map<std::string, void*> external_buffer_handle_table;
};

menoh_error_code menoh_make_model_builder(const menoh_variable_profile_table_handle vpt, menoh_model_builder_handle* dst_handle) {
    return check_error([&]() {
        *dst_handle = std::make_unique<menoh_model_builder>(menoh_model_builder{vpt->xgraph, vpt->input_profiles, vpt->output_profiles, {}})
                              .release();
        return menoh_error_code_success;
    });
}
void menoh_delete_model_builder(menoh_model_builder_handle builder) {
    delete builder;
}

menoh_error_code menoh_model_builder_attach_external_buffer(menoh_model_builder_handle builder, const char* name, void* buffer_handle) {
    return check_error([&]() {
        auto found = std::find_if(
                builder->external_buffer_handle_table.begin(), builder->external_buffer_handle_table.end(), [name](auto const& p) {
                    return name == p.first;
                });
        if (found != builder->external_buffer_handle_table.end()) {
            auto message = std::string("menoh same named variable already exist: ") + name;
            menoh_impl::set_last_error_message(message.c_str());
            return menoh_error_code_same_named_variable_already_exist;
        }
        builder->external_buffer_handle_table.emplace(std::string(name), buffer_handle);
        return menoh_error_code_success;
    });
}

/*
 * model
 */
struct menoh_model {
    std::unordered_map<std::string, menoh_impl::array_profile> variable_profiles;
    std::unique_ptr<chainerx::Context> context;
    chainerx::Device* device;
    chainer_compiler::runtime::InOuts inputs;
    chainer_compiler::runtime::InOuts outputs;
    std::unique_ptr<chainer_compiler::runtime::ChxVM> chxvm;
    chainer_compiler::runtime::ChxVMOptions chxvm_options;
    std::vector<std::shared_ptr<void>> buffer_holder;
};
void menoh_delete_model(menoh_model_handle model) {
    delete model;
}

template <typename T>
T value_or(nlohmann::json const& j, std::string const& name, T default_value) {
    return j.find(name) == j.end() ? default_value : j[name].get<T>();
}

std::shared_ptr<void> allocate_buffer(chainerx::Shape const& shape, chainerx::Dtype dtype) {
    auto bytesize = static_cast<size_t>(shape.GetTotalSize() * chainerx::GetItemSize(dtype));
    return std::shared_ptr<uint8_t>{new uint8_t[bytesize], std::default_delete<uint8_t[]>()};
}

std::shared_ptr<void> allocate_buffer(menoh_impl::array_profile const& profile) {
    return allocate_buffer(chainerx::Shape(profile.dims()), menoh_dtype_to_chx_dtype(profile.dtype()));
}

/* You can (and should) delete model_data after the model creation. */
menoh_error_code menoh_build_model(
        const menoh_model_builder_handle builder,
        const menoh_model_data_handle model_data,
        const char* backend_name,
        const char* backend_config,
        menoh_model_handle* dst_model_handle) {
    return check_error([&]() {
        auto j = nlohmann::json::parse(backend_config);

#include <menoh/json_args.inc>  // initialize global flags with `j`

        auto ctx = std::make_unique<chainerx::Context>();
        chainerx::ContextScope context_scope(*ctx);
        const std::string device_spec = value_or(j, "device", std::string(""));
        chainerx::Device* device = nullptr;
        if (device_spec.empty()) {
            device = &chainerx::GetDefaultDevice();
        } else {
            device = &ctx->GetDevice(device_spec);
        }
        chainerx::DeviceScope device_scope(*device);

        auto xgraph = *(builder->xgraph);

        // Set initializer
        CHECK(xgraph.initializer().empty());
        for (onnx::TensorProto const& xtensor : model_data->xgraph.initializer()) {
            *(xgraph.add_initializer()) = xtensor;
        }

        // Compile graph
        chainer_compiler::Graph graph(xgraph);
        {
            chainerx::NoBackpropModeScope scope;

            constexpr bool kBackprop = false;
            chainer_compiler::RunDefaultPasses(&graph, kBackprop);
            chainer_compiler::runtime::ChxVMProgramProto chxvm_prog;
            constexpr bool kDumpValueNames = false;

            chainer_compiler::chxvm::Emit(graph, &chxvm_prog, kDumpValueNames);
            auto chxvm = std::make_unique<chainer_compiler::runtime::ChxVM>(chxvm_prog);

            // Setup inputs
            chainer_compiler::runtime::InOuts inputs(chainer_compiler::runtime::LoadParams(graph));
            std::vector<std::shared_ptr<void>> buffer_holder;
            for (const chainer_compiler::Value* input : graph.input_values()) {
                if (!input->initializer()) {  // user input is input which doesn't have initializer
                    auto p = builder->input_profile_table.find(input->name());
                    CHECK(p != builder->input_profile_table.end()) << input->name() << " is not found in input_profile_table";
                    void* datap = nullptr;
                    auto found = builder->external_buffer_handle_table.find(input->name());
                    if (found != builder->external_buffer_handle_table.end()) {
                        datap = found->second;
                    } else {
                        auto data = allocate_buffer(p->second);
                        buffer_holder.push_back(data);
                        datap = data.get();
                    }
                    auto arr = chainer_compiler::runtime::MakeHostArray(
                            menoh_dtype_to_chx_dtype(p->second.dtype()), chainerx::Shape(p->second.dims()), datap);
                    auto var = std::make_shared<chainer_compiler::runtime::ChxVMVar>(std::move(arr));
                    inputs.emplace(input->name(), std::move(var));
                }
            }

            chainer_compiler::runtime::ChxVMOptions chxvm_opts;
            chxvm_opts.trace_level = value_or(j, "trace_level", 0);
            chxvm_opts.is_training = value_or(j, "is_training", false);
            chxvm_opts.check_types = value_or(j, "check_types", false);
            chxvm_opts.check_nans = value_or(j, "check_nans", false);
            chxvm_opts.check_infs = value_or(j, "check_infs", false);

            std::unordered_map<std::string, menoh_impl::array_profile> variable_profiles(
                    builder->input_profile_table.begin(), builder->input_profile_table.end());
            variable_profiles.insert(builder->output_profile_table.begin(), builder->output_profile_table.end());
            *dst_model_handle = std::make_unique<menoh_model>(menoh_model{std::move(variable_profiles),
                                                                          std::move(ctx),
                                                                          device,
                                                                          std::move(inputs),
                                                                          {},
                                                                          std::move(chxvm),
                                                                          chxvm_opts,
                                                                          std::move(buffer_holder)})
                                        .release();
        }
        return menoh_error_code_success;
    });
}

namespace {

absl::optional<chainerx::Array> menoh_model_get_variable_array(const menoh_model_handle model, const char* variable_name) {
    auto found = model->outputs.find(variable_name);
    if (found != model->outputs.end()) {
        return found->second->GetArray();
    }
    found = model->inputs.find(variable_name);
    if (found != model->inputs.end()) {
        return found->second->GetArray();
    }
    return absl::nullopt;
}

}  // namespace

menoh_error_code menoh_model_get_variable_buffer_handle(const menoh_model_handle model, const char* variable_name, void** data_p) {
    absl::optional<chainerx::Array> array = menoh_model_get_variable_array(model, variable_name);
    CHECK(array->IsContiguous());
    if (!array) {
        auto message = std::string("menoh variable not found: ") + variable_name;
        menoh_impl::set_last_error_message(message.c_str());
        return menoh_error_code_variable_not_found;
    }
    *data_p = chainer_compiler::runtime::RawStartPtr(*array);
    return menoh_error_code_success;
}

namespace impl {
template <typename F>
menoh_error_code menoh_model_get_variable_variable_attribute(const menoh_model_handle model, const char* name, F f) {
    return check_error([&]() {
        auto iter = model->variable_profiles.find(name);
        if (iter != model->variable_profiles.end()) {
            f(iter->second);
        } else {
            auto message = std::string("menoh variable not found: ") + name;
            menoh_impl::set_last_error_message(message.c_str());
            return menoh_error_code_variable_not_found;
        }
        return menoh_error_code_success;
    });
}
}  // namespace impl

menoh_error_code menoh_model_get_variable_dtype(const menoh_model_handle model, const char* variable_name, menoh_dtype* dst_dtype) {
    if (auto array = menoh_model_get_variable_array(model, variable_name)) {
        *dst_dtype = chx_dtype_to_menoh_dtype(array->dtype());
        return menoh_error_code_success;
    }
    return impl::menoh_model_get_variable_variable_attribute(
            model, variable_name, [&](menoh_impl::array_profile const& arr) { *dst_dtype = static_cast<menoh_dtype>(arr.dtype()); });
}

menoh_error_code menoh_model_get_variable_dims_size(const menoh_model_handle model, const char* variable_name, int64_t* dst_size) {
    if (auto array = menoh_model_get_variable_array(model, variable_name)) {
        *dst_size = array->ndim();
        return menoh_error_code_success;
    }
    return impl::menoh_model_get_variable_variable_attribute(
            model, variable_name, [&](menoh_impl::array_profile const& arr) { *dst_size = static_cast<int64_t>(arr.dims().size()); });
}

menoh_error_code menoh_model_get_variable_dims_at(
        const menoh_model_handle model, const char* variable_name, int64_t index, int64_t* dst_size) {
    if (auto array = menoh_model_get_variable_array(model, variable_name)) {
        CHECK_LT(index, array->ndim());
        *dst_size = array->shape()[index];
        return menoh_error_code_success;
    }
    return impl::menoh_model_get_variable_variable_attribute(
            model, variable_name, [&](menoh_impl::array_profile const& arr) { *dst_size = arr.dims().at(index); });
}

menoh_error_code menoh_model_get_variable_dims(
        const menoh_model_handle model, const char* variable_name, int64_t* dst_size, const int64_t** dims) {
    return impl::menoh_model_get_variable_variable_attribute(model, variable_name, [&](menoh_impl::array_profile const& arr) {
        *dst_size = arr.dims().size();
        *dims = arr.dims().data();
    });
}

menoh_error_code menoh_model_run(menoh_model_handle model) {
    return check_error([&]() {
        chainerx::ContextScope context_scope(*model->context);
        chainerx::DeviceScope device_scope(*model->device);
        {
            chainerx::NoBackpropModeScope scope;
            chainer_compiler::runtime::InOuts inputs(model->inputs);
            for (auto& p : inputs) {
                if (model->device != &p.second->GetArray().device()) {
                    p.second = std::make_shared<chainer_compiler::runtime::ChxVMVar>(p.second->GetArray().ToDevice(*model->device));
                }
            }

            auto outputs = model->chxvm->Run(inputs, model->chxvm_options);
            model->outputs.clear();
            for (auto p : outputs) {
                CHECK(p.second->IsArray()) << "menoh does not support non-array outputs";
                chainerx::Array array = p.second->GetArray();
                if (!chainer_compiler::runtime::IsNativeDevice(model->device)) {
                    array = array.ToNative();
                }
                array = chainerx::AsContiguous(array);
                CHECK(model->outputs.emplace(p.first, std::make_shared<chainer_compiler::runtime::ChxVMVar>(array)).second);
            }
        }
        return menoh_error_code_success;
    });
}
