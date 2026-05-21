# Serving

Pure serving-policy code shared by the hub and simulation harnesses.

This package owns backend-independent decisions about:

- which ready replica should handle a request;
- whether current demand justifies another replica;
- which worker is a valid load target under RAM, disk, backend, and cache constraints.

It must stay side-effect free. Runtime code supplies live worker/resource state,
and test simulations supply virtual worker/resource state, but both should call
the same policy functions.
