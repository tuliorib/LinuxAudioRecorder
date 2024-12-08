#!/usr/bin/env python3.12

"""
Audio Recorder App for Ubuntu with PulseAudio

This application provides a robust interface for recording audio from both
input (microphone) and output (system audio) devices using PulseAudio.
It includes proper resource management, error handling, and can be controlled
via D-Bus by a GNOME Shell extension.
"""

import os
import sys
import signal
import logging
import tempfile
import json
from pathlib import Path
from datetime import datetime
import pulsectl
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# Configure logging
LOG_FILE = Path.home() / '.local' / 'share' / 'audio-recorder' / 'recorder.log'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

class Config:
    """Configuration management for the audio recorder."""
    
    DEFAULT_CONFIG = {
        'output_dir': str(Path.home() / 'Recordings'),
        'format': 'wav',
        'sample_rate': 44100,
        'channels': 2,
        'mic_volume': 1.0,
        'system_volume': 0.8
    }
    
    def __init__(self):
        self.config_file = Path.home() / '.config' / 'audio-recorder' / 'config.json'
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.load_config()

    def load_config(self):
        """Load configuration from file or create default if not exists."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    self.settings = {**self.DEFAULT_CONFIG, **json.load(f)}
            else:
                self.settings = self.DEFAULT_CONFIG
                self.save_config()
        except Exception as e:
            logging.error(f"Error loading config: {e}")
            self.settings = self.DEFAULT_CONFIG

    def save_config(self):
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving config: {e}")

class AudioRecorder:
    """Class responsible for audio recording using PulseAudio."""

    def __init__(self):
        """Initialize the AudioRecorder with configuration and PulseAudio connection."""
        self.config = Config()
        self.pulse = pulsectl.Pulse('audio-recorder-app')
        self.is_recording = False
        self.current_recording = None
        self.modules = []  # Track loaded modules for cleanup
        
        # Ensure output directory exists
        Path(self.config.settings['output_dir']).mkdir(parents=True, exist_ok=True)

    def __del__(self):
        """Clean up resources when the object is destroyed."""
        self.cleanup()

    def get_default_source(self):
        """Get the default audio source (microphone)."""
        try:
            return self.pulse.server_info().default_source_name
        except pulsectl.PulseError as e:
            logging.error(f"Error getting default source: {e}")
            return None

    def get_default_sink(self):
        """Get the default audio sink (speakers/output)."""
        try:
            return self.pulse.server_info().default_sink_name
        except pulsectl.PulseError as e:
            logging.error(f"Error getting default sink: {e}")
            return None

    def setup_combined_recording(self):
        """Set up the combined recording of both input and output."""
        try:
            # Create a combined sink for output
            module = self.pulse.module_load('module-combine-sink',
                f'sink_name=combined_output slaves={self.get_default_sink()} ' +
                'rate=44100 channels=2')
            self.modules.append(module)
            
            # Create a loopback from the combined sink
            module = self.pulse.module_load('module-loopback',
                'sink=combined_output source_dont_move=true ' +
                f'latency_msec=1 sink_volume={self.config.settings["system_volume"]}')
            self.modules.append(module)
            
            # Create a combined source for mic + system audio
            module = self.pulse.module_load('module-combine-source',
                'source_name=combined_recording ' +
                f'sources={self.get_default_source()},combined_output.monitor ' +
                f'source_properties=device.description="Recording"')
            self.modules.append(module)
            
            return "combined_recording"
        except pulsectl.PulseError as e:
            logging.error(f"Failed to setup combined recording: {e}")
            self.cleanup()
            return None

    def generate_filename(self):
        """Generate a filename for the recording based on current time."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return Path(self.config.settings['output_dir']) / f'recording_{timestamp}.{self.config.settings["format"]}'

    def start_recording(self):
        """Start recording audio from both input and output."""
        if self.is_recording:
            logging.info("Already recording.")
            return False
            
        try:
            combined_source = self.setup_combined_recording()
            if not combined_source:
                raise Exception("Failed to create combined source")
            
            self.current_recording = self.generate_filename()
            
            # Create recording pipe and start recording
            module = self.pulse.module_load('module-pipe-source',
                f'source_name=recorder file={self.current_recording} ' +
                f'format=s16le rate={self.config.settings["sample_rate"]} ' +
                f'channels={self.config.settings["channels"]} ' +
                f'source={combined_source}')
            self.modules.append(module)
            
            self.is_recording = True
            logging.info(f"Recording started: {self.current_recording}")
            return True
            
        except Exception as e:
            logging.error(f"Error starting recording: {e}")
            self.cleanup()
            return False

    def stop_recording(self):
        """Stop recording and cleanup resources."""
        if not self.is_recording:
            logging.info("Not currently recording.")
            return False
            
        try:
            self.cleanup()
            logging.info("Recording stopped")
            return True
        except Exception as e:
            logging.error(f"Error stopping recording: {e}")
            return False

    def cleanup(self):
        """Clean up PulseAudio modules and resources."""
        try:
            # Unload all modules we created
            for module in self.modules:
                try:
                    self.pulse.module_unload(module)
                except pulsectl.PulseError:
                    pass
            self.modules.clear()
            
            self.is_recording = False
            self.current_recording = None
            
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")

class AudioRecorderService(dbus.service.Object):
    """D-Bus service for controlling the audio recorder."""
    
    def __init__(self):
        self.recorder = AudioRecorder()
        
        bus_name = dbus.service.BusName(
            'org.gnome.AudioRecorder',
            bus=dbus.SessionBus())
            
        super().__init__(
            bus_name,
            '/org/gnome/AudioRecorder')

    @dbus.service.method('org.gnome.AudioRecorder',
                        in_signature='', out_signature='b')
    def StartRecording(self):
        """D-Bus method to start recording."""
        return self.recorder.start_recording()

    @dbus.service.method('org.gnome.AudioRecorder',
                        in_signature='', out_signature='b')
    def StopRecording(self):
        """D-Bus method to stop recording."""
        return self.recorder.stop_recording()

    @dbus.service.method('org.gnome.AudioRecorder',
                        in_signature='', out_signature='b')
    def IsRecording(self):
        """D-Bus method to check recording status."""
        return self.recorder.is_recording

    @dbus.service.method('org.gnome.AudioRecorder',
                        in_signature='', out_signature='s')
    def GetCurrentRecording(self):
        """D-Bus method to get current recording filename."""
        return str(self.recorder.current_recording or '')

def signal_handler(sig, frame):
    """Handle system signals for graceful shutdown."""
    logging.info("Received shutdown signal")
    if recorder_service and recorder_service.recorder:
        recorder_service.recorder.cleanup()
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Initialize D-Bus main loop
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        
        # Create the service
        recorder_service = AudioRecorderService()
        
        # Start the main loop
        logging.info("Audio Recorder service started")
        GLib.MainLoop().run()
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)
