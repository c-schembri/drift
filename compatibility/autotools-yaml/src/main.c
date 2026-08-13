#include <stdio.h>
#include <yaml.h>

int main(void) {
    puts(yaml_get_version_string());
    return 0;
}
