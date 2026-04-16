"""Energosbyt API"""

__all__ = (
    "CONFIG_SCHEMA",
    "async_unload_entry",
    "async_reload_entry",
    "async_setup",
    "async_setup_entry",
    "config_flow",
    "const",
    "sensor",
    "DOMAIN",
)

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TYPE,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from custom_components.lkcomu_interrao._base import (
    EntitiesDataType,
    UpdateDelegatorsDataType,
)
from custom_components.lkcomu_interrao._schema import CONFIG_ENTRY_SCHEMA
from custom_components.lkcomu_interrao._util import (
    _find_existing_entry,
    _make_log_prefix,
    import_api_cls,
    is_in_russia,
    mask_username,
)
from custom_components.lkcomu_interrao.const import (
    CONF_USER_AGENT,
    DATA_YAML_CONFIG,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.lkcomu_interrao.coordinator import (
    LkcomuInterRAODataUpdateCoordinator,
)

from inter_rao_energosbyt.exceptions import EnergosbytException

if TYPE_CHECKING:
    from inter_rao_energosbyt.interfaces import BaseEnergosbytAPI

_LOGGER = logging.getLogger(__name__)


@dataclass
class LkcomuInterRAORuntimeData:
    """Runtime data for Inter RAO."""

    api: "BaseEnergosbytAPI"
    coordinator: LkcomuInterRAODataUpdateCoordinator
    final_config: ConfigType
    entities: EntitiesDataType
    update_delegators: UpdateDelegatorsDataType
    is_in_russia: bool
    provider_icons: dict[str, str] = field(default_factory=dict)
    dev_classes_processed: set[str] = field(default_factory=set)


type LkcomuInterRAOConfigEntry = config_entries.ConfigEntry[LkcomuInterRAORuntimeData]


def _unique_entries(value: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    pairs: dict[tuple[str, str], int | None] = {}

    errors = []
    for i, config in enumerate(value):
        pair = (config[CONF_TYPE], config[CONF_USERNAME])
        if pair in pairs:
            if pairs[pair] is not None:
                errors.append(
                    vol.Invalid(
                        "duplicate unique key, first encounter", path=[pairs[pair]]
                    )
                )
                pairs[pair] = None
            errors.append(
                vol.Invalid("duplicate unique key, subsequent encounter", path=[i])
            )
        else:
            pairs[pair] = i

    if errors:
        if len(errors) > 1:
            raise vol.MultipleInvalid(errors)
        raise next(iter(errors))

    return value


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Any(
            vol.Equal({}),
            vol.All(
                cv.ensure_list,
                vol.Length(min=1),
                [CONFIG_ENTRY_SCHEMA],
                _unique_entries,
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up the Inter RAO component."""
    domain_config = config.get(DOMAIN)
    if not domain_config:
        return True

    domain_data = {}
    hass.data[DOMAIN] = domain_data

    yaml_config = {}
    domain_data[DATA_YAML_CONFIG] = yaml_config

    for user_cfg in domain_config:
        if not user_cfg:
            continue

        type_: str = user_cfg[CONF_TYPE]
        username: str = user_cfg[CONF_USERNAME]

        key = (type_, username)
        log_prefix = f"[{type_}/{mask_username(username)}] "

        _LOGGER.debug(
            log_prefix
            + (
                "Получена конфигурация из YAML"
                if is_in_russia(hass)
                else "YAML configuration encountered"
            )
        )

        existing_entry = _find_existing_entry(hass, type_, username)
        if existing_entry:
            if existing_entry.source == config_entries.SOURCE_IMPORT:
                yaml_config[key] = user_cfg
                _LOGGER.debug(
                    log_prefix
                    + (
                        "Соответствующая конфигурационная запись существует"
                        if is_in_russia(hass)
                        else "Matching config entry exists"
                    )
                )
            else:
                _LOGGER.warning(
                    log_prefix
                    + (
                        "Конфигурация из YAML переопределена другой конфигурацией!"
                        if is_in_russia(hass)
                        else "YAML config is overridden by another entry!"
                    )
                )
            continue

        # Save YAML configuration
        yaml_config[key] = user_cfg

        _LOGGER.warning(
            log_prefix
            + (
                "Создание новой конфигурационной записи"
                if is_in_russia(hass)
                else "Creating new config entry"
            )
        )

        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data={
                    CONF_TYPE: type_,
                    CONF_USERNAME: username,
                },
            )
        )

    if not yaml_config:
        _LOGGER.debug(
            "Конфигурация из YAML не обнаружена"
            if is_in_russia(hass)
            else "YAML configuration not found"
        )

    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry: LkcomuInterRAOConfigEntry
) -> bool:
    type_ = config_entry.data[CONF_TYPE]
    username = config_entry.data[CONF_USERNAME]
    unique_key = (type_, username)
    entry_id = config_entry.entry_id
    log_prefix = f"[{type_}/{mask_username(username)}] "

    # Source full configuration
    if config_entry.source == config_entries.SOURCE_IMPORT:
        # Source configuration from YAML
        domain_data = hass.data.get(DOMAIN) or {}
        yaml_config = domain_data.get(DATA_YAML_CONFIG)

        if not yaml_config or unique_key not in yaml_config:
            _LOGGER.info(
                log_prefix
                + (
                    f"Удаление записи {entry_id} после удаления из конфигурации YAML"
                    if is_in_russia(hass)
                    else f"Removing entry {entry_id} after removal from YAML configuration"
                )
            )
            hass.async_create_task(hass.config_entries.async_remove(entry_id))
            return False

        user_cfg = yaml_config[unique_key]

    else:
        # Config entry data was already validated by config flow;
        # only check required fields are present
        entry_data = dict(config_entry.data)
        if not all(k in entry_data for k in (CONF_TYPE, CONF_USERNAME, CONF_PASSWORD)):
            _LOGGER.error(
                log_prefix
                + (
                    "Сохранённая конфигурация повреждена: отсутствуют обязательные поля"
                    if is_in_russia(hass)
                    else "Configuration invalid: missing required fields"
                )
            )
            return False

        user_cfg = entry_data

        # Apply options overrides (user_agent from options takes precedence)
        if config_entry.options:
            if CONF_USER_AGENT in config_entry.options:
                user_cfg[CONF_USER_AGENT] = config_entry.options[CONF_USER_AGENT]

    _LOGGER.info(
        log_prefix
        + (
            "Применение конфигурационной записи"
            if is_in_russia(hass)
            else "Applying configuration entry"
        )
    )

    try:
        api_cls = await import_api_cls(hass, type_)
    except (ImportError, AttributeError):
        _LOGGER.error(
            log_prefix
            + (
                (
                    "Невозможно найти тип API. Это фатальная ошибка для компонента. "
                    "Пожалуйста, обратитесь к разработчику (или заявите о проблеме на GitHub)."
                )
                if is_in_russia(hass)
                else (
                    "Could not find API type. This is a fatal error for the component. "
                    "Please, report it to the developer (or open an issue on GitHub)."
                )
            )
        )
        return False

    api_object = api_cls(
        username=username,
        password=user_cfg[CONF_PASSWORD],
        user_agent=user_cfg.get(CONF_USER_AGENT),
    )

    # Authenticate before first data fetch
    try:
        auth_task = asyncio.ensure_future(api_object.async_authenticate())
        done, _ = await asyncio.wait({auth_task}, timeout=60)
        if not done:
            auth_task.cancel()
            await api_object.async_close()
            raise ConfigEntryNotReady("Authentication timed out after 60s")
        auth_task.result()
    except (ConfigEntryNotReady, ConfigEntryAuthFailed):
        raise
    except (TimeoutError, OSError, asyncio.TimeoutError) as e:
        await api_object.async_close()
        raise ConfigEntryNotReady(
            f"Connection error: {e}"
        ) from e
    except EnergosbytException as e:
        await api_object.async_close()
        _LOGGER.error(
            log_prefix
            + (
                f"Ошибка авторизации: {e}"
                if is_in_russia(hass)
                else f"Authentication failed: {e}"
            )
        )
        raise ConfigEntryAuthFailed(
            f"Authentication failed: {e}"
        ) from e
    except Exception as e:
        await api_object.async_close()
        raise ConfigEntryNotReady(
            f"Unexpected error during authentication: {e}"
        ) from e

    # Setup coordinator
    scan_interval = DEFAULT_SCAN_INTERVAL
    if CONF_SCAN_INTERVAL in user_cfg:
        if isinstance(user_cfg[CONF_SCAN_INTERVAL], timedelta):
            scan_interval = user_cfg[CONF_SCAN_INTERVAL].total_seconds()
        elif isinstance(user_cfg[CONF_SCAN_INTERVAL], (int, float)):
            scan_interval = user_cfg[CONF_SCAN_INTERVAL]

    coordinator = LkcomuInterRAODataUpdateCoordinator(
        hass,
        api_object,
        name=f"{DOMAIN}_{type_}_{username}",
        update_interval=timedelta(seconds=scan_interval),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await api_object.async_close()
        raise
    except ConfigEntryNotReady:
        await api_object.async_close()
        raise
    except Exception as e:
        await api_object.async_close()
        raise ConfigEntryNotReady(f"Error connecting to API: {e}") from e

    accounts = coordinator.data

    if not accounts:
        # Cancel setup because no accounts provided
        _LOGGER.warning(
            log_prefix
            + (
                "Лицевые счета не найдены"
                if is_in_russia(hass)
                else "No accounts found"
            )
        )
        await api_object.async_close()
        return False

    _LOGGER.debug(
        log_prefix
        + (
            f"Найдено {len(accounts)} лицевых счетов"
            if is_in_russia(hass)
            else f"Found {len(accounts)} accounts"
        )
    )

    # Create options update listener (auto-cleaned on unload)
    config_entry.async_on_unload(
        config_entry.add_update_listener(async_reload_entry)
    )

    config_entry.runtime_data = LkcomuInterRAORuntimeData(
        api=api_object,
        coordinator=coordinator,
        final_config=user_cfg,
        entities={},
        update_delegators={},
        is_in_russia=is_in_russia(hass),
    )

    # Forward entry setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(
        config_entry,
        [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN],
    )

    _LOGGER.debug(
        log_prefix
        + (
            "Применение конфигурации успешно"
            if is_in_russia(hass)
            else "Setup successful"
        )
    )
    return True


async def async_reload_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
) -> bool:
    """Reload Lkcomu InterRAO entry"""
    log_prefix = _make_log_prefix(config_entry, "setup")
    _LOGGER.info(
        log_prefix
        + (
            "Перезагрузка интеграции"
            if is_in_russia(hass)
            else "Reloading configuration entry"
        )
    )
    return await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: LkcomuInterRAOConfigEntry
) -> bool:
    """Unload Lkcomu InterRAO entry"""
    log_prefix = _make_log_prefix(config_entry, "setup")

    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]
    )

    if unload_ok:
        await config_entry.runtime_data.api.async_close()

        _LOGGER.info(
            log_prefix
            + (
                "Интеграция выгружена"
                if is_in_russia(hass)
                else "Unloaded configuration entry"
            )
        )

    else:
        _LOGGER.warning(
            log_prefix
            + (
                "При выгрузке конфигурации произошла ошибка"
                if is_in_russia(hass)
                else "Failed to unload configuration entry"
            )
        )

    return unload_ok
