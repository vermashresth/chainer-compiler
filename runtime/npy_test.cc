#include <stdio.h>

#include <gtest/gtest.h>

#include <chainerx/array.h>
#include <chainerx/routines/creation.h>
#include <chainerx/testing/context_session.h>

#include <runtime/npy.h>

namespace chainer_compiler {
namespace runtime {
namespace {

TEST(NpyTest, SaveNpy) {
    chainerx::testing::ContextSession sess;

    chainerx::Array a = chainerx::Eye(2, absl::nullopt, absl::nullopt, chainerx::Dtype::kFloat32);
    SaveNpy(a, "out/t.npy");

    std::string actual(144, '\0');
    FILE* fp = fopen("out/t.npy", "rb");
    ASSERT_EQ(144, fread(&actual[0], 1, 144, fp));
    fclose(fp);

    std::string expected(
            "\x93NUMPY\x01\x00v\x00{'desc"
            "r': '<f4', 'fort"
            "ran_order': Fals"
            "e, 'shape': (2, "
            "2), }           "
            "                "
            "                "
            "               \n"
            "\x00\x00\x80?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x80?",
            144);
    EXPECT_EQ(expected, actual);
}

}  // namespace
}  // namespace runtime
}  // namespace chainer_compiler
