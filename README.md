# Screen Recorder Web App

This is a simple web application built with Flask and OpenCV to record the screen.
It allows starting/stopping recording, setting duration and FPS, specifying a recording region,
and optionally formatting the output for YouTube Shorts (1080x1920).
Recorded videos are saved in the `recordings` directory.

## Setup

1.  **Clone or download the code.**
2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    # Activate the environment
    # On Windows (Git Bash or WSL):
    # source venv/Scripts/activate
    # On macOS/Linux:
    # source venv/bin/activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Running the Application

1.  **Make sure your virtual environment is activated.**
2.  **Run the Flask application:**
    ```bash
    python app.py
    ```
3.  **Open your web browser** and navigate to `http://127.0.0.1:5000` (or the address shown in the terminal).

## Usage

-   Use the web interface to configure recording settings (duration, FPS, Shorts format, recording region).
-   Click "録画開始" (Start Recording) to begin.
-   Click "録画停止" (Stop Recording) to stop manually (or wait for the duration to complete).
-   The list of recordings will update automatically.
-   You can download or delete recordings from the list.

## Notes

-   Screen recording can be resource-intensive. Performance depends on your hardware, screen resolution, and chosen FPS.
-   The `mp4v` codec is used by default. If you encounter issues playing the recorded files, you might need to experiment with other codecs (requires code changes in `app.py` and potentially installing codec libraries).
-   Recording might require specific screen access permissions depending on your operating system.
-   The application creates a `recordings` folder in the same directory where you run `app.py` to store the video files. 