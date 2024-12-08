#!/usr/bin/env python3

"""
Enhanced Audio Recorder with quality controls, volume management, and monitoring
"""

import time
import sys
import logging
import subprocess
import threading
import argparse
from pathlib import Path
from datetime import datetime
import pulsectl
import numpy as np
import sounddevice as sd

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

class AudioConfig:
    """Configuration management for audio recording"""
    DEFAULT_CONFIG = {
        'output_dir': str(Path.home() / 'Recordings'),
        'format': 'wav',
        'sample_rate': 44100,
        'channels': 2,
        'mic_volume': 1.5,    # Increased default mic volume
        'system_volume': 1.2,  # Increased default system volume
        'bit_depth': '32float'  # Options: 16, 24, 32float
    }
    
    def __init__(self, **kwargs):
        self.settings = {**self.DEFAULT_CONFIG, **kwargs}
        self._create_output_dir()
    
    def _create_output_dir(self):
        """Create output directory if it doesn't exist"""
        Path(self.settings['output_dir']).mkdir(parents=True, exist_ok=True)

class AudioLevelMonitor:
    """Monitor audio levels during recording"""
    def __init__(self, device_name):
        self.device_name = device_name
        self.running = False
        self._thread = None
        
    def start(self):
        """Start monitoring audio levels"""
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop)
        self._thread.daemon = True
        self._thread.start()
        
    def stop(self):
        """Stop monitoring audio levels"""
        self.running = False
        if self._thread:
            self._thread.join()
    
    def _monitor_loop(self):
        """Monitor audio levels and print meter"""
        def callback(indata, frames, time, status):
            if status:
                logging.warning(f"Audio monitoring status: {status}")
            volume_norm = np.linalg.norm(indata) * 10
            meter = '|' * int(volume_norm)
            print(f'\rAudio Level: {meter.ljust(60)}', end='', flush=True)
        
        try:
            with sd.InputStream(device=self.device_name,
                              callback=callback):
                while self.running:
                    time.sleep(0.1)
        except Exception as e:
            logging.error(f"Error monitoring audio: {e}")

def setup_recording(pulse, config):
    """Set up PulseAudio for recording with volume control"""
    try:
        sink = pulse.server_info().default_sink_name
        source = pulse.server_info().default_source_name
        
        logging.info(f"Default sink: {sink}")
        logging.info(f"Default source: {source}")
        
        # Create combined sink with higher volume
        sink_module = pulse.module_load('module-combine-sink',
                                      f'sink_name=combined_sink slaves={sink} ' +
                                      f'rate={config.settings["sample_rate"]} channels={config.settings["channels"]}')
        
        # Create loopback with system volume control
        loopback_module = pulse.module_load('module-loopback',
                                          'sink=combined_sink source_dont_move=true ' +
                                          f'latency_msec=1 sink_volume={int(config.settings["system_volume"] * 65536)}')
        
        time.sleep(1)
        
        # Create recording sink with enhanced quality
        source_module = pulse.module_load('module-null-sink',
                                        'sink_name=recording_sink ' +
                                        f'rate={config.settings["sample_rate"]} ' +
                                        f'channels={config.settings["channels"]} ' +
                                        'sink_properties=device.description="Recording"')
        
        # Create loopbacks with volume control
        loopback_mic = pulse.module_load('module-loopback',
                                        f'source={source} sink=recording_sink ' +
                                        f'sink_volume={int(config.settings["mic_volume"] * 65536)}')
        
        loopback_audio = pulse.module_load('module-loopback',
                                         'source=combined_sink.monitor sink=recording_sink ' +
                                         f'sink_volume={int(config.settings["system_volume"] * 65536)}')
        
        logging.info("Recording setup completed with volume adjustments")
        return [sink_module, loopback_module, source_module, loopback_mic, loopback_audio]
    
    except Exception as e:
        logging.error(f"Error setting up recording: {e}")
        return None

def record_audio(duration, output_file, config):
    """Record audio with quality settings"""
    try:
        format_flags = {
            '16': [],
            '24': ['--format=s24le'],
            '32float': ['--format=float32le']
        }
        
        cmd = [
            'parec',
            '--device=recording_sink.monitor',
            '-d', str(duration),
            '--channels', str(config.settings['channels']),
            '--rate', str(config.settings['sample_rate']),
            '--file-format=wav',
            *format_flags[config.settings['bit_depth']],
            output_file
        ]
        
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Recording failed: {e}")
        return False

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Enhanced Audio Recorder')
    parser.add_argument('--duration', type=int, default=10,
                       help='Recording duration in seconds')
    parser.add_argument('--output-dir', type=str,
                       help='Output directory for recordings')
    parser.add_argument('--sample-rate', type=int,
                       choices=[44100, 48000, 96000], default=44100,
                       help='Sample rate in Hz')
    parser.add_argument('--bit-depth', type=str,
                       choices=['16', '24', '32float'], default='32float',
                       help='Bit depth for recording')
    parser.add_argument('--mic-volume', type=float, default=1.5,
                       help='Microphone volume multiplier')
    parser.add_argument('--system-volume', type=float, default=1.2,
                       help='System audio volume multiplier')
    
    args = parser.parse_args()
    
    # Create configuration
    config_kwargs = {
        'sample_rate': args.sample_rate,
        'bit_depth': args.bit_depth,
        'mic_volume': args.mic_volume,
        'system_volume': args.system_volume
    }
    if args.output_dir:
        config_kwargs['output_dir'] = args.output_dir
    
    config = AudioConfig(**config_kwargs)
    pulse = pulsectl.Pulse('test-recorder')
    modules = None
    monitor = None
    
    try:
        print("Setting up recording...")
        modules = setup_recording(pulse, config)
        if not modules:
            print("Failed to set up recording!")
            return 1
        
        # Generate output filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = Path(config.settings['output_dir']) / f'recording_{timestamp}.wav'
        
        # Start audio level monitoring
        monitor = AudioLevelMonitor('recording_sink.monitor')
        monitor.start()
        
        print(f"\nStarting {args.duration}-second recording to {output_file}")
        print("Recording with settings:")
        print(f"  Sample Rate: {config.settings['sample_rate']} Hz")
        print(f"  Bit Depth: {config.settings['bit_depth']}")
        print(f"  Mic Volume: {config.settings['mic_volume']}x")
        print(f"  System Volume: {config.settings['system_volume']}x")
        print("\nAudio levels (press Ctrl+C to stop):")
        
        if record_audio(args.duration, str(output_file), config):
            print(f"\nRecording saved to {output_file}")
        else:
            print("\nRecording failed!")
            return 1
            
    except KeyboardInterrupt:
        print("\nRecording interrupted!")
    finally:
        if monitor:
            monitor.stop()
        cleanup_modules(pulse, modules)
        pulse.close()
    
    return 0

def cleanup_modules(pulse, modules):
    """Clean up PulseAudio modules"""
    if modules:
        for module in modules:
            try:
                pulse.module_unload(module)
            except:
                pass

if __name__ == "__main__":
    sys.exit(main())
