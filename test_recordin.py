#!/usr/bin/env python3.12

"""
Test script for AudioRecorder that records both system and microphone audio
for 10 seconds and saves it as test.wav
"""

import time
import sys
from pathlib import Path
from audio_recorder import AudioRecorder, Config

def main():
    # Create an instance of AudioRecorder
    recorder = AudioRecorder()
    
    # Override the default filename
    recorder.config.settings['output_dir'] = str(Path.cwd())  # Current directory
    recorder.config.settings['format'] = 'wav'
    
    print("Starting 10-second test recording...")
    
    # Start recording
    if not recorder.start_recording():
        print("Failed to start recording!")
        return 1
        
    try:
        # Record for 10 seconds
        time.sleep(10)
        
        # Stop recording
        recorder.stop_recording()
        print(f"Recording saved as: {recorder.current_recording}")
        
    except KeyboardInterrupt:
        print("\nRecording interrupted!")
        recorder.stop_recording()
        return 1
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
