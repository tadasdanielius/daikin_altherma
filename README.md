# Daikin altherma custom component for Home assistant

This HA integration is for BRP069A62 (should work with BRP069A61) Daikin Altherma LAN adapter.
The integration connects to device locally and it does not need internet access to work.

**NOTE**: This is a very early release, and comes without any guarantees. So, use it at your own risk.

# Installation

You can install manually or by using [HACS](https://hacs.xyz/) (the easier way). 
## HACS

1. Go to `HACS > Integrations`
2. Press `+ explore and & download repositories`
3. Search for `Daikin Altherma`


## Manual installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `daikin_altherma`.
4. Download _all_ the files from the `custom_components/daikin_altherma/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. It should automatically discover your adapter !

If device is not discovered automatically you can go to "Configuration" -> "Integrations" click "+" and search for "Daikin Altherma HVAC"


# Features

This integration allows to control the following options

**For water heating:**
 - Get/Set target temperature
 - Get water temperature
 - Turn off/on
 - Turn powerful

**For space heating:**
 - works with both states weather dependent and fixed
 - Operation State (heating/cooling/auto)
 - Leaving water temperature heating/cooling/auto (get/set)
 - Leaving water temperature offset heating/cooling/auto (get/set)
 - Indoor temperature
 - Outdoor temperature
 - Current leaving water temperature
 - Turn on/off
 - Operation mode

## Screenshots

![Daikin Altherma space heating and domestic hot water](https://raw.githubusercontent.com/tadasdanielius/daikin_altherma/main/img/ha_altherma1.png)

![Domestic hot water control](https://raw.githubusercontent.com/tadasdanielius/daikin_altherma/main/img/ha_altherma2.png)

![Energy Consumption](https://github.com/tadasdanielius/daikin_altherma/blob/main/img/HA_energy_consumption_sensor.png)

# Climate control component

Daikin device allows to control leaving water temperature which is not what you expect your room temperature to be it is either higher or lower (if cooling is turned on). 
So, in order to have thermostat type control you can use [generic thermostat](https://www.home-assistant.io/integrations/generic_thermostat/) with the integration

Put this into your `configuration.yaml`:

```yaml
climate:
  - platform: generic_thermostat
    name: My Heater
    unique_id: daikin_climate_control
    target_sensor: sensor.indoor_temperature
    min_temp: 18
    max_temp: 26
    heater: switch.climate_control
    target_temp: 20
    cold_tolerance: 0.3
    hot_tolerance: 0.3
    ac_mode: false
    initial_hvac_mode: "heat"
    min_cycle_duration:
      seconds: 5
``` 

`target_sensor` can be any temperature sensor. 


<a href="https://www.buymeacoffee.com/buymeacoff7" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-black.png" width="150px" height="35px" alt="Buy Me A Coffee" style="height: 35px !important;width: 150px !important;" ></a>
