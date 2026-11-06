"""`dots cloud`: run the cloud next to a hub."""

from __future__ import annotations

from aiohttp import web

from ..system.util import dots_home, load_account_alias, load_account_name, load_or_create_account
from .config import CloudConfig
from .hub import HubClient
from .server import Cloud
from .store import Store


def build(config: CloudConfig) -> web.Application:
    """The cloud for the account on this machine, with its state in dots_home()/cloud.db."""
    me = load_or_create_account()
    name = load_account_name() or load_account_alias() or "You"
    cloud = Cloud(Store(dots_home() / "cloud.db"), HubClient(config.hub_url, config.hub_token), config, me=me, my_name=name)
    return cloud.app()


def main(config: CloudConfig | None = None) -> None:
    config = config or CloudConfig.from_env()
    print(f"dots cloud on http://{config.host}:{config.port}, reading the hub at {config.hub_url}")
    web.run_app(build(config), host=config.host, port=config.port, print=None)


if __name__ == "__main__":
    main()
