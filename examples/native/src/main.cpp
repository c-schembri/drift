#include "add.h"

#include <stdio.h>

int main() {
    int result = add(20, 22);
    printf("%d\n", result);
    return result == 42 ? 0 : 1;
}
