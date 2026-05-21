# Generation

Generation strategies own token policy.

The default strategy is autoregressive decoding. It calls
`BackendRuntime` for backend-specific primitives such as sampling,
prefill, decode, and token text conversion.

Future strategies such as MTP, Medusa, prompt lookup, or EAGLE belong
here, but their model-specific primitives should still live behind the
backend interface.
