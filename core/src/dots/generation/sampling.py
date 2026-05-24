"""Compatibility module for older imports.

Sampling is backend-owned because logits arrays are backend-specific.
Use ``BackendRuntime.sample_token`` in generation code.
"""
