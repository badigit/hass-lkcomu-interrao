"""DataUpdateCoordinator for lkcomu_interrao integration"""

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from inter_rao_energosbyt.exceptions import EnergosbytException, ResponseCodeError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from inter_rao_energosbyt.interfaces import Account, AccountID, BaseEnergosbytAPI

_LOGGER = logging.getLogger(__name__)

_AUTH_ERROR_CODE = 201
_API_TIMEOUT = 120  # seconds


def _is_auth_error(error: Exception) -> bool:
    """Check if the error indicates an authentication failure (code 201).

    The error may be ResponseCodeError, EnergosbytException, or a chained
    exception. We check int(error), scan all args for code 201, and also
    check string representation as a fallback.
    """
    try:
        if int(error) == _AUTH_ERROR_CODE:
            return True
    except (ValueError, TypeError):
        pass

    for arg in error.args:
        try:
            if int(arg) == _AUTH_ERROR_CODE:
                return True
        except (ValueError, TypeError):
            continue

    if "NOT_AUTHENTICATED" in str(error.args) or ": 201" in str(error.args):
        return True

    return False


class LkcomuInterRAODataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from Inter RAO API."""

    def __init__(
        self,
        hass: "HomeAssistant",
        api: "BaseEnergosbytAPI",
        name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialize."""
        self.api = api
        super().__init__(hass, _LOGGER, name=name, update_interval=update_interval)

    async def _async_update_data(self) -> dict["AccountID", "Account"]:
        """Update data via library."""
        try:
            task = asyncio.ensure_future(
                self.api.async_update_accounts(with_related=True)
            )
            done, _ = await asyncio.wait({task}, timeout=_API_TIMEOUT)
            if not done:
                task.cancel()
                raise UpdateFailed(
                    f"API request timed out after {_API_TIMEOUT}s"
                )
            return task.result()
        except UpdateFailed:
            raise
        except ResponseCodeError as error:
            if _is_auth_error(error):
                raise ConfigEntryAuthFailed(
                    "Session expired, re-authentication required"
                ) from error
            raise UpdateFailed(f"Error communicating with API: {error}") from error
        except EnergosbytException as error:
            if _is_auth_error(error):
                raise ConfigEntryAuthFailed(
                    "Session expired, re-authentication required"
                ) from error
            raise UpdateFailed(f"Error communicating with API: {error}") from error
        except Exception as error:
            if _is_auth_error(error):
                raise ConfigEntryAuthFailed(
                    "Session expired, re-authentication required"
                ) from error
            raise UpdateFailed(f"Unexpected error: {error}") from error
