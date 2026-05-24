# System

Local machine utilities and resource policy.

`resources.py` discovers bounded worker resources such as RAM, disk, CPU,
I/O, throughput, and backend names.

`model_cache.py` owns prepared-artifact cache eviction within the worker's
disk budget. Backends should reserve cache space here before conversion
or other disk-heavy preparation.
