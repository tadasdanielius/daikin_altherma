# Daikin Altherma integration

The integration uses the local Daikin LAN adapter API through the version of `pyaltherma` pinned in the integration manifest. See the compatibility and development section of README.md for test environments and commands.

Each config entry has its own coordinator and API connection. Entities read cached status, send commands through the API transaction lock, and request a coordinator refresh after writes. Use the unit profile to determine which operations and sensors a device supports. Domestic hot water units can appear as either `DomesticHotWaterTank` or `DomesticHotWater`.
