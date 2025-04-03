# check_stereo_mixer.py
import logging
import os
import queue
import time

import sounddevice as sd
import soundfile as sf

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Configuration ---
TARGET_DEVICE_INDEX = 2  # MME Index for Stereo Mixer
OUTPUT_FILENAME = "stereo_mixer_test.wav"
DURATION = 10  # seconds
# Use the settings found for MME Stereo Mixer
SAMPLERATE = 44100
CHANNELS = 2
# ---------------------

audio_queue = queue.Queue()
data_written = False  # Flag to check if any audio data was actually written


def audio_callback(indata, frames, time, status):
    """Callback function to put audio data into the queue."""
    if status:
        logging.warning(f"Callback status: {status}")
    audio_queue.put(indata.copy())


def record_stereo_mixer():
    """Records audio from the Stereo Mixer to a WAV file."""
    global data_written
    stream = None
    wav_file = None
    logging.info(
        f"Attempting to record from MME device {TARGET_DEVICE_INDEX} ('Stereo Mixer') for {DURATION} seconds..."
    )

    try:
        # Verify device existence and capabilities (optional but recommended)
        try:
            device_info = sd.query_devices(TARGET_DEVICE_INDEX)
            if not isinstance(device_info, dict):
                logging.error(f"Could not get device info for index {TARGET_DEVICE_INDEX}.")
                return False
            if device_info.get("max_input_channels", 0) < CHANNELS:
                logging.error(
                    f"Device {TARGET_DEVICE_INDEX} ({device_info.get('name')}) does not support {CHANNELS} input channels."
                )
                return False
            if device_info.get("hostapi") != 0:  # Assuming MME is Host API 0 from previous logs
                logging.warning(
                    f"Device {TARGET_DEVICE_INDEX} does not belong to MME (Host API 0). Found API {device_info.get('hostapi')}. Attempting anyway..."
                )

            logging.info(
                f"Target device: {device_info.get('name')}, SR: {SAMPLERATE}, Ch: {CHANNELS}"
            )
        except ValueError:
            logging.error(f"Device with index {TARGET_DEVICE_INDEX} not found.")
            return False
        except Exception as e:
            logging.error(f"Error querying device {TARGET_DEVICE_INDEX}: {e}")
            return False

        # Open WAV file for writing
        wav_file = sf.SoundFile(
            OUTPUT_FILENAME, mode="w", samplerate=SAMPLERATE, channels=CHANNELS, format="WAV"
        )
        logging.info(f"Opened '{OUTPUT_FILENAME}' for writing.")

        # Create and start the input stream
        stream = sd.InputStream(
            samplerate=SAMPLERATE,
            channels=CHANNELS,
            device=TARGET_DEVICE_INDEX,
            callback=audio_callback,
        )
        stream.start()
        logging.info("Recording started... Make sure system audio is playing!")

        # Record for the specified duration
        start_time = time.time()
        while time.time() - start_time < DURATION:
            try:
                # Get data from queue and write to file
                data = audio_queue.get(timeout=0.1)
                wav_file.write(data)
                if not data_written:
                    data_written = True  # Mark that we have received and written data
            except queue.Empty:
                # Expected timeout if no new data
                pass
            except Exception as e:
                logging.error(f"Error writing to file: {e}")
                break  # Stop recording on write error

        logging.info("Recording duration finished.")
        return True  # Indicate that the recording process completed

    except sd.PortAudioError as e:
        logging.error(f"PortAudioError during recording setup or stream: {e}")
        return False
    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")
        return False
    finally:
        # Ensure stream and file are closed
        if stream:
            try:
                if not stream.closed:
                    stream.stop()
                    stream.close()
                logging.info("Audio stream closed.")
            except Exception as e:
                logging.error(f"Error closing stream: {e}")
        if wav_file:
            try:
                wav_file.close()
                logging.info(f"File '{OUTPUT_FILENAME}' closed.")
            except Exception as e:
                logging.error(f"Error closing file: {e}")


if __name__ == "__main__":
    print(f"--- Stereo Mixer Recording Test (MME Index {TARGET_DEVICE_INDEX}) ---")
    print(f"Will record for {DURATION} seconds to '{OUTPUT_FILENAME}'")
    print("Please play some system audio during the test.")
    time.sleep(2)  # Give user time to prepare

    process_completed = record_stereo_mixer()

    print("--- Test Results ---")
    if process_completed:
        print("Recording process completed.")
        try:
            file_size = os.path.getsize(OUTPUT_FILENAME)
            print(f"Output file: '{OUTPUT_FILENAME}', Size: {file_size} bytes")
            if file_size <= 44:  # WAV header is ~44 bytes
                print("WARNING: File size is very small, likely contains no audio data.")
            elif not data_written:
                print(
                    "WARNING: Recording process finished, but no audio data was actually written to the file."
                )
            else:
                print("SUCCESS: File seems to contain audio data. Please verify by listening.")
        except FileNotFoundError:
            print(f"ERROR: File '{OUTPUT_FILENAME}' was expected but not found.")
        except OSError as e:
            print(f"ERROR: Could not get file size for '{OUTPUT_FILENAME}': {e}")
    else:
        print("ERROR: Recording process failed to complete due to errors logged above.")
        if os.path.exists(OUTPUT_FILENAME):
            print(f"WARNING: An empty or incomplete file '{OUTPUT_FILENAME}' might still exist.")
