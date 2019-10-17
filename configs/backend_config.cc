#include <configs/backend_config.h>

#include <iostream>

#include <common/log.h>
#include <configs/json_repository.h>

namespace chainer_compiler {

class BackendConfigImpl : public BackendConfig {
public:
    explicit BackendConfigImpl(const std::string& name, const json& config) : name_(name) {
        CHECK(config.is_object()) << config;
        for (const auto& el : config.items()) {
            if (el.key() == "simplify_preproc") {
                ParseFlags("simplify", el.value(), &simplify_preproc_);
            } else if (el.key() == "simplify") {
                ParseFlags("simplify", el.value(), &simplify_);
            } else if (el.key() == "supported_ops") {
                ParseFlags("supported ops", el.value(), &supported_ops_);
                supported_ops_set_ = true;
            } else if (el.key() == "merge") {
                ParseFlags("merge", el.value(), &merge_);
            } else if (el.key() == "expanding_functions") {
                ParseFlags("expanding functions", el.value(), &expanding_functions_);
            } else {
                std::cerr << "WARNING: Unknown backend config: " << el.key() << std::endl;
            }
        }

        for (const std::string& n : simplify_preproc_) {
            simplify_.emplace(n);
        }
    }

    ~BackendConfigImpl() override = default;

    const std::string& name() const override {
        return name_;
    }

    const std::set<std::string>& GetSimplifyPreproc() const override {
        return simplify_preproc_;
    }

    const std::set<std::string>& GetSimplify() const override {
        return simplify_;
    }

    const std::set<std::string>& GetMerge() const override {
        return merge_;
    }

    const std::set<std::string>& GetExpandingFunctions() const override {
        return expanding_functions_;
    }

    bool HasOp(const std::string& op) const override {
        if (!supported_ops_set_) return true;
        return supported_ops_.count(op) > 0;
    }

private:
    void ParseFlags(const std::string& name, const json& simplify, std::set<std::string>* names) {
        CHECK(simplify.is_object()) << name << " must be an object: " << simplify;
        for (const auto& el : simplify.items()) {
            CHECK(el.value().is_boolean()) << name << " values must be bool: " << simplify;
            if (el.value() == false) {
                continue;
            }
            CHECK(names->emplace(el.key()).second) << "Duplicate key: " << el.key();
        }
    }

    std::string name_;
    std::set<std::string> simplify_preproc_;
    std::set<std::string> simplify_;
    bool supported_ops_set_{false};
    std::set<std::string> supported_ops_;
    std::set<std::string> merge_;
    std::set<std::string> expanding_functions_;
};

std::unique_ptr<BackendConfig> BackendConfig::FromName(const std::string& name) {
    json j = LoadJSONFromName(name);
    return std::make_unique<BackendConfigImpl>(name, j);
}

std::unique_ptr<BackendConfig> BackendConfig::FromJSON(const std::string& json_str) {
    json j = LoadJSONFromString(json_str);
    return std::make_unique<BackendConfigImpl>("custom", j);
}

}  // namespace chainer_compiler
