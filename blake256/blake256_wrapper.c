/*
 * Python C extension wrapper for sphlib Blake-256 (8-round variant)
 * Used by Blakecoin for block header proof-of-work hashing.
 *
 * This wraps the exact same sphlib implementation used in the Blakecoin daemon
 * to guarantee hash compatibility.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "sph_blake.h"

/*
 * blake256_hash(data) -> bytes
 *
 * Compute the 8-round Blake-256 hash of the input data.
 * Returns a 32-byte hash digest.
 */
static PyObject* py_blake256_hash(PyObject* self, PyObject* args) {
    const unsigned char *data;
    Py_ssize_t len;

    if (!PyArg_ParseTuple(args, "y#", &data, &len))
        return NULL;

    sph_blake256_context ctx;
    unsigned char hash[32];

    sph_blake256_init(&ctx);
    sph_blake256(&ctx, data, (size_t)len);
    sph_blake256_close(&ctx, hash);

    return PyBytes_FromStringAndSize((const char *)hash, 32);
}

static PyMethodDef Blake256Methods[] = {
    {"hash", py_blake256_hash, METH_VARARGS,
     "Compute Blake-256 (8-round sphlib) hash of input bytes. Returns 32-byte digest."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef blake256module = {
    PyModuleDef_HEAD_INIT,
    "_blake256",
    "Blake-256 (8-round sphlib) hash function used by Blakecoin",
    -1,
    Blake256Methods
};

PyMODINIT_FUNC PyInit__blake256(void) {
    return PyModule_Create(&blake256module);
}
