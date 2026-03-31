/* Synthetic test library for isolate-elf unit tests.
 * Exports: 3 FUNC symbols, 1 OBJECT symbol.
 */

#include <stddef.h>

/* Public FUNC symbols */
int testlib_add(int a, int b) {
    return a + b;
}

int testlib_multiply(int a, int b) {
    return a * b;
}

const char *testlib_greeting(void) {
    return "hello from testlib";
}

/* Public OBJECT symbol */
const int testlib_version = 42;

/* Internal function (should be hidden by visibility) */
__attribute__((visibility("hidden")))
int _testlib_internal(int x) {
    return x * 2;
}
