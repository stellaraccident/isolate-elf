/* Test library with symbol versioning.
 * Version tags are applied by the version script (testlib_versioned.map),
 * not by .symver directives.
 */

int versioned_func_a(int x) {
    return x + 1;
}

int versioned_func_b(int x) {
    return x + 2;
}

const int versioned_data = 99;
