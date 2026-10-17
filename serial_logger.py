#!/usr/bin/env python3

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
    from serial import SerialException
except ImportError:
    print("ERROR: pyserial is not installed.")
    print("Install it using: sudo apt install python3-serial")
    sys.exit(1)


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

DEFAULT_BAUD = 115200
DEFAULT_OUTPUT_DIR = "KriSHE_logs"

# Matches temperature lines such as:
# TOP (CS7): 25.00 °C
# MIDDLE (CS15): 618.75 °C
# BOTTOM (CS4): 24.75 °C

TEMPERATURE_PATTERN = re.compile(
    r"(?P<sensor>TOP|MIDDLE|BOTTOM)\s*"
    r"\(CS\d+\)\s*:\s*"
    r"(?P<temperature>-?\d+(?:\.\d+)?)\s*"
    r"(?:°\s*C|deg(?:rees)?\s*C|C)",
    re.IGNORECASE,
)

UPTIME_PATTERN = re.compile(
    r"uptime\s*[:=]\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

STATE_PATTERN = re.compile(
    r"(?:kiln\s*)?state\s*[:=]\s*([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

DIE_TEMP_PATTERN = re.compile(
    r"(?:chip\s*)?(?:die\s*)?temp(?:erature)?\s*[:=]\s*"
    r"(-?\d+(?:\.\d+)?)\s*°?\s*C?",
    re.IGNORECASE,
)

SENSOR_FAULT_PATTERN = re.compile(
    r"SENSOR FAULT|PROBE OPEN|DISCONNECTED|INVALID",
    re.IGNORECASE,
)


def timestamp_now():
    """Return a readable local timestamp."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def safe_filename_timestamp():
    """Return a timestamp suitable for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_temperature(line):
    """Extract sensor name and temperature from a serial line."""
    match = TEMPERATURE_PATTERN.search(line)

    if not match:
        return None

    return (
        match.group("sensor").upper(),
        float(match.group("temperature")),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Continuously log ESP32 serial output and temperatures."
    )

    parser.add_argument(
        "--port",
        required=True,
        help="Serial port, e.g. /dev/ttyACM0 or /dev/ttyUSB0",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Serial baud rate (default: {DEFAULT_BAUD})",
    )

    parser.add_argument(
        "--outdir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for log files (default: {DEFAULT_OUTPUT_DIR})",
    )

    args = parser.parse_args()

    output_dir = Path(args.outdir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = safe_filename_timestamp()

    log_path = output_dir / f"krishe_serial_{timestamp}.log"
    csv_path = output_dir / f"krishe_temperatures_{timestamp}.csv"

    print("=" * 65)
    print("           KriSHE Carbon - ESP32 Serial Logger")
    print("=" * 65)
    print(f"Serial port : {args.port}")
    print(f"Baud rate   : {args.baud}")
    print(f"Output dir  : {output_dir.resolve()}")
    print(f"Raw log     : {log_path}")
    print(f"Temperature : {csv_path}")
    print("=" * 65)
    print("Press Ctrl+C to stop logging.\n")

    # Latest temperature values are retained until a new value arrives.
    temperatures = {
        "TOP": None,
        "MIDDLE": None,
        "BOTTOM": None,
    }

    sensor_status = {
        "TOP": "UNKNOWN",
        "MIDDLE": "UNKNOWN",
        "BOTTOM": "UNKNOWN",
    }

    uptime_s = ""
    kiln_state = ""
    chip_die_c = ""

    # Open files in line-buffered mode so logs are written continuously.
    try:
        with open(
            log_path,
            "a",
            encoding="utf-8",
            buffering=1,
        ) as log_file, open(
            csv_path,
            "a",
            newline="",
            encoding="utf-8",
            buffering=1,
        ) as csv_file:

            writer = csv.writer(csv_file)

            # Add CSV header if the file is empty.
            if csv_path.stat().st_size == 0:
                writer.writerow([
                    "host_timestamp",
                    "device_uptime_s",
                    "kiln_state",
                    "chip_die_c",
                    "top_c",
                    "top_status",
                    "middle_c",
                    "middle_status",
                    "bottom_c",
                    "bottom_status",
                ])

            try:
                with serial.Serial(
                    port=args.port,
                    baudrate=args.baud,
                    timeout=1,
                ) as ser:

                    print("Connected. Logging started.\n")

                    # Some ESP32 boards reset when the serial port opens.
                    time.sleep(0.2)

                    while True:
                        raw = ser.readline()

                        if not raw:
                            continue

                        line = raw.decode(
                            "utf-8",
                            errors="replace",
                        ).rstrip("\r\n")

                        host_timestamp = timestamp_now()

                        # Save every serial line, including ESP_LOG output.
                        log_file.write(
                            f"[{host_timestamp}] {line}\n"
                        )

                        # Print the live serial stream to the terminal.
                        print(line, flush=True)

                        # Capture device uptime when present.
                        match = UPTIME_PATTERN.search(line)
                        if match:
                            uptime_s = match.group(1)

                        # Capture kiln/controller state when present.
                        match = STATE_PATTERN.search(line)
                        if match:
                            kiln_state = match.group(1)

                        # Capture ESP32 chip temperature when present.
                        match = DIE_TEMP_PATTERN.search(line)
                        if match:
                            chip_die_c = match.group(1)

                        # Extract temperature readings.
                        reading = extract_temperature(line)

                        if reading:
                            sensor, temperature = reading

                            temperatures[sensor] = temperature
                            sensor_status[sensor] = "OK"

                            # A complete diagnostic block normally ends
                            # with the BOTTOM sensor reading.
                            if sensor == "BOTTOM":
                                writer.writerow([
                                    host_timestamp,
                                    uptime_s,
                                    kiln_state,
                                    chip_die_c,
                                    temperatures["TOP"],
                                    sensor_status["TOP"],
                                    temperatures["MIDDLE"],
                                    sensor_status["MIDDLE"],
                                    temperatures["BOTTOM"],
                                    sensor_status["BOTTOM"],
                                ])

                                csv_file.flush()
                                log_file.flush()

                                print(
                                    f"\n[CSV SAVED] {host_timestamp} | "
                                    f"TOP={temperatures['TOP']} °C | "
                                    f"MIDDLE={temperatures['MIDDLE']} °C | "
                                    f"BOTTOM={temperatures['BOTTOM']} °C\n",
                                    flush=True,
                                )

                        # Detect explicit sensor fault messages.
                        elif SENSOR_FAULT_PATTERN.search(line):
                            # The fault is always preserved in the raw log.
                            # Mark the relevant sensor invalid if its name
                            # appears in the same line.
                            upper_line = line.upper()

                            for sensor in ("TOP", "MIDDLE", "BOTTOM"):
                                if sensor in upper_line:
                                    sensor_status[sensor] = "FAULT"

            except KeyboardInterrupt:
                print("\n\nLogging stopped by user.")

            except SerialException as exc:
                print(f"\nSerial communication error: {exc}")
                print("Check the USB cable, port, and serial monitor.")

    except SerialException as exc:
        print(f"\nCould not open serial port {args.port}: {exc}")
        print("\nCheck available ports with:")
        print("  ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")
        print("\nAlso close PlatformIO or other serial monitors.")
        sys.exit(1)

    except PermissionError:
        print(f"\nPermission denied accessing {args.port}.")
        print("Try adding your user to the dialout group:")
        print("  sudo usermod -aG dialout $USER")
        print("Then log out and log back in.")
        sys.exit(1)

    finally:
        print("\nLog files:")
        print(f"  Raw serial : {log_path.resolve()}")
        print(f"  Temperatures: {csv_path.resolve()}")
        print("\nYour saved logs will remain on your laptop.")


if __name__ == "__main__":
    main()