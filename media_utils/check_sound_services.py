# media_utils/check_sounde_services.py を少し変更

import sys

import sounddevice as sd

print("\\n--- Devices reported by MME ---")
try:
    # MME の Host API情報を取得
    mme_api_info = None
    for api_info in sd.query_hostapis():
        if isinstance(api_info, dict) and api_info.get("name") == "MME":
            mme_api_info = api_info
            break

    if mme_api_info and "devices" in mme_api_info:
        print(f"Found MME Host API: {mme_api_info['name']}")
        print(f"Device indices for MME: {mme_api_info['devices']}")

        # MME に属する各デバイスの詳細情報を取得して表示
        for device_index in mme_api_info["devices"]:
            try:
                device_info = sd.query_devices(device=device_index)
                if isinstance(device_info, dict):
                    # 入力デバイスのみ表示 (In > 0)
                    if device_info.get("max_input_channels", 0) > 0:
                        print(
                            f"  Input Index {device_info.get('index', 'N/A')}: {device_info.get('name', 'N/A')} (In: {device_info.get('max_input_channels', 'N/A')})"
                        )
                else:
                    print(
                        f"  Could not get detailed info for device index {device_index} (unexpected format: {type(device_info)})",
                        file=sys.stderr,
                    )
            except Exception as e_inner:
                print(
                    f"  Error querying details for device index {device_index}: {e_inner}",
                    file=sys.stderr,
                )
    elif not mme_api_info:
        print("MME Host API not found.")
    else:
        print("MME Host API info does not contain 'devices' list.")
except Exception as e:
    print(f"An error occurred: {e}", file=sys.stderr)
