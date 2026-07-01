#include "sph_blake.h"
void blake256_hash(const unsigned char *data, unsigned int len, unsigned char *out) {
    sph_blake256_context ctx;
    sph_blake256_init(&ctx);
    sph_blake256(&ctx, data, len);
    sph_blake256_close(&ctx, out);
}
