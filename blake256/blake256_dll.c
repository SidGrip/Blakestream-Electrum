#include "sph_blake.h"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
EXPORT void blake256_hash(const unsigned char *data, unsigned int len, unsigned char *out) {
    sph_blake256_context ctx;
    sph_blake256_init(&ctx);
    sph_blake256(&ctx, data, len);
    sph_blake256_close(&ctx, out);
}
