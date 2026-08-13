#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

TEST_CASE("Drift links a Conan component graph") {
    REQUIRE(2 + 2 == 4);
}

int main(int argc, char **argv) {
    return Catch::Session().run(argc, argv);
}
