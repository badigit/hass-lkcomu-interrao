"""DataUpdateCoordinator for lkcomu_interrao integration"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from inter_rao_energosbyt.exceptions import EnergosbytException

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from inter_rao_energosbyt.interfaces import Account, AccountID, BaseEnergosbytAPI

_LOGGER = logging.getLogger(__name__)


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
            # We fetch accounts and their details
            # If the library's async_update_accounts is truly async, this is fine.
            # If not, we already wrap it in with_auto_auth which might handle it.
            return await self.api.async_update_accounts(with_related=True)
        except EnergosbytException as error:
            raise UpdateFailed(f"Error communicating with API: {error}") from error
        except Exception as error:
            raise UpdateFailed(f"Unexpected error: {error}") from error
