#include <ini.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int setting(void *user, char const *section, char const *name, char const *value) {
    int *answer = user;
    if (strcmp(section, "drift") == 0 && strcmp(name, "answer") == 0) {
        *answer = atoi(value);
    }
    return 1;
}

int main(void) {
    int answer = 0;
    char const *document = "[drift]\nanswer=42\n";
    if (ini_parse_string(document, setting, &answer) != 0) {
        return 1;
    }
    printf("%d\n", answer);
    return answer == 42 ? 0 : 1;
}
