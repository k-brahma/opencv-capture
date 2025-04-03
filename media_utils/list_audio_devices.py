import sounddevice as sd

try:
    print("Querying available audio devices...\n")
    print(sd.query_devices())
    print("\nFinished querying devices.")
    print("\nLook for devices that might represent system audio output/loopback.")
    print(
        "Examples: 'Stereo Mix', 'Loopback', 'What U Hear', '内部オーディオ アナログステレオ' etc."
    )
    print("Note the 'index' number (e.g., > 1) or 'name' of the desired device.")
    print("The device with '>' is the default input, '<' is the default output.")

    # Also list host APIs
    print("\nAvailable Host APIs:")
    print(sd.query_hostapis())
    print("\nNote: WASAPI on Windows often supports loopback recording.")

except Exception as e:
    print(f"An error occurred while querying devices: {e}")
