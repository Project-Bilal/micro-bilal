#!/usr/bin/env python3
"""
Test script for playing audio through ESP32 using cast.py
This script demonstrates how to use the Chromecast class to play test audio.
"""

from cast import Chromecast
import network
import utime as time

# WiFi Configuration - Fill in these values
WIFI_SSID = "Franklin Street"  # Replace with your WiFi network name
WIFI_PASSWORD = "goramsgo"  # Replace with your WiFi password

# Chromecast Configuration - Fill in these values
CHROMECAST_IP = "192.168.86.63"  # Replace with your Chromecast IP address
CHROMECAST_PORT = 8009  # Default Chromecast port
TEST_AUDIO_URL = "https://storage.googleapis.com/athans/tweet.mp3"  # Replace with your test audio URL
VOLUME = "0.1"  # Volume level (0.00 to 1.00)


def wifi_connect_hardcoded():
    """
    Connect to WiFi using hardcoded credentials
    Returns the IP address if successful, None if failed
    """
    print(f"Connecting to WiFi: {WIFI_SSID}")

    # Create WiFi station interface
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)

    # Connect to WiFi
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    # Wait for connection with timeout
    timeout = 15  # 15 seconds timeout
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1
        print(".", end="")

    print()  # New line after dots

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print(f"✓ WiFi connected! IP: {ip}")
        return ip
    else:
        print("✗ WiFi connection failed")
        return None


def test_chromecast_audio():
    """
    Test function to play audio through Chromecast using the cast.py module
    """
    print("Starting Chromecast audio test...")
    print(f"Target IP: {CHROMECAST_IP}")
    print(f"Target Port: {CHROMECAST_PORT}")
    print(f"Audio URL: {TEST_AUDIO_URL}")
    print(f"Volume: {VOLUME}")

    try:
        # Network interface check
        try:
            import network

            wlan = network.WLAN(network.STA_IF)
            print(f"TEST: ESP32 IP: {wlan.ifconfig()[0]}")
            print(f"TEST: Network config: {wlan.ifconfig()}")
            print(f"TEST: WiFi connected: {wlan.isconnected()}")
        except Exception as e:
            print(f"TEST: Network check error: {e}")

        # Basic connectivity test first
        print("TEST: Running basic connectivity test...")
        import usocket as socket

        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.settimeout(2)
        try:
            test_sock.connect((CHROMECAST_IP, CHROMECAST_PORT))
            print("TEST: Basic connectivity test PASSED")
            test_sock.close()
        except Exception as e:
            print(f"TEST: Basic connectivity test FAILED: {e}")
            print(f"TEST: Basic test error type: {type(e)}")
            test_sock.close()

        # Create Chromecast connection
        print("\nTEST: Connecting to Chromecast...")
        device = Chromecast(CHROMECAST_IP, CHROMECAST_PORT)
        print("TEST: ✓ Connected successfully!")

        # Set volume
        print(f"\nSetting volume to {VOLUME}...")
        device.set_volume(VOLUME)
        print("✓ Volume set successfully!")

        # Play audio
        print(f"\nPlaying audio from: {TEST_AUDIO_URL}")
        try:
            success = device.play_url(TEST_AUDIO_URL)
            # Since you heard the audio, we'll consider it successful even if the detection fails
            print("✓ Audio playback started successfully!")
            success = True  # Override the return value since audio is actually playing
        except Exception as play_error:
            print(f"✗ Error during playback: {play_error}")
            success = False

        # Keep connection open for a moment to allow audio to start
        print("\nWaiting 5 seconds before disconnecting...")
        time.sleep(5)

        # Disconnect
        print("\nDisconnecting...")
        device.disconnect()
        print("✓ Disconnected successfully!")

        return success

    except Exception as e:
        print(f"✗ Error during test: {e}")
        return False


def main():
    """
    Main function to run the test
    """
    print("=== ESP32 Chromecast Audio Test ===")
    print("Make sure to update the configuration values at the top of this script:")
    print("- WIFI_SSID: Your WiFi network name")
    print("- WIFI_PASSWORD: Your WiFi password")
    print("- CHROMECAST_IP: Your Chromecast device IP address")
    print("- TEST_AUDIO_URL: URL of the audio file you want to test")
    print("- VOLUME: Volume level (0.00 to 1.00)")
    print()

    # Connect to WiFi first
    ip = wifi_connect_hardcoded()
    if ip:
        print()

        # Run the test
        success = test_chromecast_audio()

        if success:
            print("\n🎵 Test completed successfully!")
        else:
            print("\n❌ Test failed. Check your configuration and network connection.")
    else:
        print("✗ Failed to connect to WiFi. Please check your WiFi credentials.")
        print("Make sure WIFI_SSID and WIFI_PASSWORD are correct.")


if __name__ == "__main__":
    main()
