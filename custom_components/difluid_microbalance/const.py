DOMAIN = "difluid_microbalance"

SERVICE_UUID_MICROBALANCE = "000000ee-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_MICROBALANCE = "0000ff01-0000-1000-8000-00805f9b34fb"

SERVICE_UUID_MICROBALANCE_TI = "000000dd-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_MICROBALANCE_TI = "0000aa01-0000-1000-8000-00805f9b34fb"

CONF_IS_TI = "is_ti"

DEVICE_STATUS_MAP = {
    0: "Power Down",
    1: "Charging",
    2: "Low Power Mode 1",
    3: "Low-Battery Shutdown",
    4: "Startup",
    5: "Idle",
    6: "Show Device Information",
    7: "Tare in Progress",
    8: "OTA in Progress",
    9: "OTA Failed",
    10: "Timing in Progress",
    11: "Timer Pause",
    12: "Reserved",
    13: "Low Power Mode 2",
    14: "Auto Stop Timing Trigger",
}

WEIGHT_UNITS = {0: "g", 1: "oz", 2: "gr"}
