import asyncio
import datetime
import functools
import re
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TYPE, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import EntityPlatform
from inter_rao_energosbyt.enums import ProviderType
from inter_rao_energosbyt.exceptions import EnergosbytException

from custom_components.lkcomu_interrao.const import DOMAIN

if TYPE_CHECKING:
    from inter_rao_energosbyt.interfaces import BaseEnergosbytAPI


def _make_log_prefix(
    config_entry: Any | ConfigEntry, domain: Any | EntityPlatform, *args
):
    join_args = [
        (
            config_entry.entry_id[-6:]
            if isinstance(config_entry, ConfigEntry)
            else str(config_entry)
        ),
        (domain.domain if isinstance(domain, EntityPlatform) else str(domain)),
    ]
    if args:
        join_args.extend(map(str, args))

    return "[" + "][".join(join_args) + "] "


@callback
def _find_existing_entry(
    hass: HomeAssistant, type_: str, username: str
) -> config_entries.ConfigEntry | None:
    existing_entries = hass.config_entries.async_entries(DOMAIN)
    for config_entry in existing_entries:
        if (
            config_entry.data[CONF_TYPE] == type_
            and config_entry.data[CONF_USERNAME] == username
        ):
            return config_entry


async def import_api_cls(hass: HomeAssistant, type_: str) -> type["BaseEnergosbytAPI"]:
    """Import API class by type."""
    module = await hass.async_add_executor_job(
        functools.partial(
            __import__,
            "inter_rao_energosbyt.api." + type_,
            globals(),
            locals(),
            ("API",),
        )
    )
    return module.API


_RE_USERNAME_MASK = re.compile(r"^(\W*)(.).*(.)$")


def mask_username(username: str):
    parts = username.split("@")
    return "@".join(map(lambda x: _RE_USERNAME_MASK.sub(r"\1\2***\3", x), parts))


_RE_FAVICON = re.compile(r'["\']?REACT_APP_FAVICON["\']?\s*:\s*"([\w.]+\.ico)"')

ICONS_FOR_PROVIDERS: dict[str, asyncio.Future | str | None] = {}


def _make_code_search_index(code):
    return tuple(map(str.lower, (code + "Logo", "defaultMarker" + code)))


async def async_get_icons_for_providers(
    api: "BaseEnergosbytAPI", provider_types: set[int]
) -> dict[str, str]:
    session = api._session  # noqa: SLF001
    # Explanation: BaseEnergosbytAPI does not provide a public session object,
    # and this function needs to perform additional requests to the same base URL
    # to fetch provider icons.
    base_url = api.BASE_URL
    icons = {}

    async with session.get(base_url + "/asset-manifest.json") as response:
        manifest = await response.json()

    iter_types = []

    for provider_type in provider_types:
        try:
            code = ProviderType(provider_type).name.lower()
        except (ValueError, TypeError):
            continue
        else:
            iter_types.append(code)

    for code in iter_types:
        search_index = _make_code_search_index(code)
        if "_" in code:
            root_code = code.split("_")[0]
            search_index = (*search_index, *_make_code_search_index(root_code))
        for key in manifest:
            lower_key = key.lower()
            for index_key in search_index:
                if index_key in lower_key:
                    icons[code] = base_url + "/" + manifest[key]
                    break

            if (
                code not in icons
                and code in key
                and (
                    lower_key.endswith(".png")
                    or lower_key.endswith(".jpg")
                    or lower_key.endswith(".svg")
                )
            ):
                icons[code] = base_url + "/" + manifest[key]

    # Diversion for ProviderType.TKO
    if (
        ProviderType.TKO.name.lower() not in icons
        and ProviderType.MES.name.lower() in icons
    ):
        icons[ProviderType.TKO.name.lower()] = icons[ProviderType.MES.name.lower()]

    if "main.js" in manifest:
        async with session.get(base_url + "/" + manifest["main.js"]) as response:
            js_code = await response.text()

        m = _RE_FAVICON.search(js_code)
        if m:
            url = base_url + "/" + m.group(1)
            for code in iter_types:
                icons.setdefault(code, url)

    return icons


def is_in_russia(hass: HomeAssistant) -> bool:
    """Check if the Home Assistant instance is in Russia."""
    if hass.config.country == "RU":
        return True

    tz = hass.config.time_zone
    if tz and (
        tz.startswith("Europe/Moscow")
        or tz.startswith("Asia/")
        or tz.startswith("Europe/Samara")
        or tz.startswith("Europe/Saratov")
        or tz.startswith("Europe/Ulyanovsk")
        or tz.startswith("Europe/Astrakhan")
        or tz.startswith("Europe/Volgograd")
        or tz.startswith("Europe/Kaliningrad")
        or tz.startswith("Europe/Kirov")
    ):
        return True

    return False
_T = TypeVar("_T")
_RT = TypeVar("_RT")


async def with_auto_auth(
    api: "BaseEnergosbytAPI",
    async_getter: Callable[..., Coroutine[Any, Any, _RT]],
    *args,
    **kwargs,
) -> _RT:
    try:
        return await async_getter(*args, **kwargs)
    except EnergosbytException:
        await api.async_authenticate()
        return await async_getter(*args, **kwargs)
