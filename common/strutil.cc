#include "strutil.h"

#if defined(_MSC_VER)
#include <BaseTsd.h>
typedef SSIZE_T ssize_t;
#endif

namespace chainer_compiler {

std::vector<std::string> SplitString(const std::string& str, const std::string& sep) {
    std::vector<std::string> ret;
    if (str.empty()) return ret;
    size_t index = 0;
    while (true) {
        size_t next = str.find(sep, index);
        ret.push_back(str.substr(index, next - index));
        if (next == std::string::npos) break;
        index = next + 1;
    }
    return ret;
}

bool HasPrefix(const std::string& str, const std::string& prefix) {
    ssize_t size_diff = str.size() - prefix.size();
    return size_diff >= 0 && str.substr(0, prefix.size()) == prefix;
}

bool HasSuffix(const std::string& str, const std::string& suffix) {
    ssize_t size_diff = str.size() - suffix.size();
    return size_diff >= 0 && str.substr(size_diff) == suffix;
}

std::string Basename(const std::string& str) {
    std::size_t found = str.rfind('/');
    if (found == std::string::npos) return str;
    return str.substr(found + 1);
}

}  // namespace chainer_compiler
