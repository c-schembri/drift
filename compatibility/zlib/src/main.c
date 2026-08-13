#include <string.h>
#include <zlib.h>

int main(void) {
    const char input[] = "drift compatibility";
    unsigned char compressed[64];
    unsigned char output[64];
    uLongf compressed_size = sizeof(compressed);
    uLongf output_size = sizeof(output);
    if (compress(compressed, &compressed_size, (const Bytef *)input, sizeof(input)) != Z_OK) {
        return 1;
    }
    if (uncompress(output, &output_size, compressed, compressed_size) != Z_OK) {
        return 2;
    }
    return output_size == sizeof(input) && memcmp(input, output, sizeof(input)) == 0 ? 0 : 3;
}
