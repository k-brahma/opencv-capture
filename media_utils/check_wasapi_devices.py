# check_wasapi_devices.py
import logging
import sys

import sounddevice as sd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

print("\n--- Input Devices reported by WASAPI ---")
try:
    wasapi_api_index = -1
    host_apis = sd.query_hostapis()
    for i, api_info in enumerate(host_apis):
        if isinstance(api_info, dict) and api_info.get("name") == "Windows WASAPI":
            wasapi_api_index = i
            logging.info(f"Found WASAPI Host API at index: {wasapi_api_index}")
            break
    if wasapi_api_index == -1:
        print("WASAPI Host API not found.")
        sys.exit()

    all_devices = sd.query_devices()
    if not isinstance(all_devices, list):
        all_devices = [all_devices] if isinstance(all_devices, dict) else []

    found_input = False
    for device_info in all_devices:
        if (
            isinstance(device_info, dict)
            and device_info.get("hostapi") == wasapi_api_index
            and device_info.get("max_input_channels", 0) > 0
        ):
            print(
                f"  Input Index {device_info.get('index', 'N/A')}: {device_info.get('name', 'N/A')} (In: {device_info.get('max_input_channels', 'N/A')})"
            )
            found_input = True

    if not found_input:
        print("No input devices found for WASAPI.")

except Exception as e:
    print(f"An error occurred: {e}", file=sys.stderr)
