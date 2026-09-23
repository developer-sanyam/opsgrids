"""`python -m platform_core.ops_monitor` — the edge box's monitor process.

Runs in the `product` image, the same way `python -m platform_core.edge_relay` does (see
`compose.prod.yml`'s `ops-monitor` service): no new image, no service-specific code, importing
no service's `app`.
"""

from __future__ import annotations

from platform_core.ops_monitor.main import main

if __name__ == "__main__":
    main()
