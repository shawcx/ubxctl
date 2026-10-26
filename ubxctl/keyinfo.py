"""Human-readable annotations for commonly used configuration keys.

pyubx2 supplies names and types for every documented key; this adds
descriptions, units and enum labels for the ones people usually touch.
Keys without an entry still work, they just show a generic description.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Info:
    desc: str
    unit: str = ""
    enum: dict[int, str] = field(default_factory=dict)
    # Changing this can cut off the connection the TUI is using.
    danger: str = ""


_TIMEREF = {0: "UTC", 1: "GPS", 2: "GLONASS", 3: "BeiDou", 4: "Galileo", 5: "NavIC"}
_PROT_LOCKOUT = "Disabling this on UART1 will stop the TUI from talking to the receiver."

INFO: dict[str, Info] = {
    # Rate
    "CFG-RATE-MEAS": Info("Nominal time between GNSS measurements (100 = 10 Hz, 1000 = 1 Hz)", "ms"),
    "CFG-RATE-NAV": Info("Number of measurements per navigation solution", "cycles"),
    "CFG-RATE-TIMEREF": Info("Time system to which measurements are aligned", enum=_TIMEREF),
    # Navigation engine
    "CFG-NAVSPG-DYNMODEL": Info("Dynamic platform model", enum={
        0: "Portable", 2: "Stationary", 3: "Pedestrian", 4: "Automotive", 5: "Sea",
        6: "Airborne <1g", 7: "Airborne <2g", 8: "Airborne <4g", 9: "Wrist", 10: "Bike",
        11: "Mower", 12: "E-scooter"}),
    "CFG-NAVSPG-FIXMODE": Info("Position fix mode", enum={1: "2D only", 2: "3D only", 3: "Auto 2D/3D"}),
    "CFG-NAVSPG-UTCSTANDARD": Info("UTC standard used for time output", enum={
        0: "Auto", 3: "USNO (GPS)", 5: "European (Galileo)", 6: "Soviet (GLONASS)",
        7: "NTSC (BeiDou)", 8: "NPLI (NavIC)"}),
    "CFG-NAVSPG-INIFIX3D": Info("Initial fix must be 3D"),
    "CFG-NAVSPG-WKNROLLOVER": Info("GPS week rollover number", "weeks"),
    "CFG-NAVSPG-USE_PPP": Info("Use precise point positioning"),
    "CFG-NAVSPG-ACKAIDING": Info("Acknowledge assistance input messages"),
    "CFG-NAVSPG-INFIL_MINSVS": Info("Minimum number of satellites for navigation"),
    "CFG-NAVSPG-INFIL_MAXSVS": Info("Maximum number of satellites for navigation"),
    "CFG-NAVSPG-INFIL_MINCNO": Info("Minimum satellite signal level for navigation", "dBHz"),
    "CFG-NAVSPG-INFIL_MINELEV": Info("Minimum elevation for a satellite to be used", "deg"),
    "CFG-NAVSPG-INFIL_NCNOTHRS": Info("Number of satellites required above CNO threshold for a fix"),
    "CFG-NAVSPG-INFIL_CNOTHRS": Info("C/N0 threshold for deciding whether to attempt a fix", "dBHz"),
    "CFG-NAVSPG-OUTFIL_PDOP": Info("Output filter: position DOP mask", "0.1"),
    "CFG-NAVSPG-OUTFIL_TDOP": Info("Output filter: time DOP mask", "0.1"),
    "CFG-NAVSPG-OUTFIL_PACC": Info("Output filter: position accuracy mask", "m"),
    "CFG-NAVSPG-OUTFIL_TACC": Info("Output filter: time accuracy mask", "m"),
    "CFG-NAVSPG-OUTFIL_FACC": Info("Output filter: frequency accuracy mask", "cm/s"),
    "CFG-NAVSPG-CONSTR_ALT": Info("Fixed altitude (mean sea level) for 2D fix mode", "cm"),
    "CFG-NAVSPG-CONSTR_ALTVAR": Info("Fixed altitude variance for 2D mode", "cm^2"),
    "CFG-NAVSPG-CONSTR_DGNSSTO": Info("DGNSS timeout", "s"),
    "CFG-MOT-GNSSSPEED_THRS": Info("Static hold: speed threshold (0 = off)", "cm/s"),
    "CFG-MOT-GNSSDIST_THRS": Info("Static hold: distance threshold before leaving static hold", "m"),
    # UART1
    "CFG-UART1-BAUDRATE": Info("UART1 baud rate. The TUI follows the receiver to the new rate.", "baud",
                               danger="The serial link will switch to the new baud rate."),
    "CFG-UART1-STOPBITS": Info("UART1 stop bits", enum={0: "0.5", 1: "1", 2: "1.5", 3: "2"},
                               danger="The TUI only supports 8N1."),
    "CFG-UART1-DATABITS": Info("UART1 data bits", enum={0: "8", 1: "7"}, danger="The TUI only supports 8N1."),
    "CFG-UART1-PARITY": Info("UART1 parity", enum={0: "None", 1: "Odd", 2: "Even"},
                             danger="The TUI only supports 8N1."),
    "CFG-UART1-ENABLED": Info("Enable UART1", danger="Disabling UART1 disconnects the TUI."),
    "CFG-UART1INPROT-UBX": Info("Accept UBX input on UART1", danger=_PROT_LOCKOUT),
    "CFG-UART1INPROT-NMEA": Info("Accept NMEA input on UART1"),
    "CFG-UART1OUTPROT-UBX": Info("Send UBX output on UART1", danger=_PROT_LOCKOUT),
    "CFG-UART1OUTPROT-NMEA": Info("Send NMEA output on UART1"),
    # NMEA
    "CFG-NMEA-PROTVER": Info("NMEA protocol version", enum={21: "2.1", 23: "2.3", 40: "4.0", 41: "4.10", 42: "4.11"}),
    "CFG-NMEA-MAXSVS": Info("Max satellites per GSV talker", enum={0: "Unlimited", 8: "8", 12: "12", 16: "16"}),
    "CFG-NMEA-COMPAT": Info("Compatibility mode (fixed-width fields)"),
    "CFG-NMEA-CONSIDER": Info("Consideration mode"),
    "CFG-NMEA-LIMIT82": Info("Limit sentences to 82 characters"),
    "CFG-NMEA-HIGHPREC": Info("High-precision mode (more decimals in position)"),
    "CFG-NMEA-SVNUMBERING": Info("Satellite numbering", enum={0: "Strict", 1: "Extended"}),
    "CFG-NMEA-MAINTALKERID": Info("Main talker ID", enum={
        0: "Auto", 1: "GP", 2: "GL", 3: "GN", 4: "GA", 5: "GB", 7: "GQ"}),
    "CFG-NMEA-GSVTALKERID": Info("GSV talker ID", enum={0: "Per GNSS", 1: "Main talker ID"}),
    "CFG-NMEA-OUT_INVFIX": Info("Output invalid fixes"),
    "CFG-NMEA-OUT_MSKFIX": Info("Output masked fixes"),
    "CFG-NMEA-OUT_INVTIME": Info("Output invalid time"),
    "CFG-NMEA-OUT_INVDATE": Info("Output invalid date"),
    "CFG-NMEA-OUT_ONLYGPS": Info("Restrict output to GPS satellites"),
    "CFG-NMEA-OUT_FROZENCOG": Info("Output course over ground even when frozen"),
    # Signals
    "CFG-SIGNAL-GPS_ENA": Info("Enable GPS"),
    "CFG-SIGNAL-GPS_L1CA_ENA": Info("Enable GPS L1 C/A"),
    "CFG-SIGNAL-GPS_L5_ENA": Info("Enable GPS L5"),
    "CFG-SIGNAL-SBAS_ENA": Info("Enable SBAS"),
    "CFG-SIGNAL-SBAS_L1CA_ENA": Info("Enable SBAS L1 C/A"),
    "CFG-SIGNAL-GAL_ENA": Info("Enable Galileo"),
    "CFG-SIGNAL-GAL_E1_ENA": Info("Enable Galileo E1"),
    "CFG-SIGNAL-GAL_E5A_ENA": Info("Enable Galileo E5a"),
    "CFG-SIGNAL-BDS_ENA": Info("Enable BeiDou"),
    "CFG-SIGNAL-BDS_B1_ENA": Info("Enable BeiDou B1I"),
    "CFG-SIGNAL-BDS_B1C_ENA": Info("Enable BeiDou B1C"),
    "CFG-SIGNAL-BDS_B2A_ENA": Info("Enable BeiDou B2a"),
    "CFG-SIGNAL-QZSS_ENA": Info("Enable QZSS"),
    "CFG-SIGNAL-QZSS_L1CA_ENA": Info("Enable QZSS L1 C/A"),
    "CFG-SIGNAL-QZSS_L1S_ENA": Info("Enable QZSS L1S (SLAS augmentation)"),
    "CFG-SIGNAL-QZSS_L5_ENA": Info("Enable QZSS L5"),
    "CFG-SIGNAL-HEALTH_L5": Info("Use L5 signals even when flagged unhealthy (pre-operational GPS L5)"),
    "CFG-SIGNAL-GLO_ENA": Info("Enable GLONASS"),
    "CFG-SIGNAL-NAVIC_ENA": Info("Enable NavIC"),
    "CFG-SIGNAL-NAVIC_L5_ENA": Info("Enable NavIC L5"),
    # Interference / antenna
    "CFG-ITFM-ENABLE": Info("Enable jamming/interference monitor"),
    "CFG-ITFM-BBTHRESHOLD": Info("Broadband jamming detection threshold"),
    "CFG-ITFM-CWTHRESHOLD": Info("CW jamming detection threshold"),
    "CFG-ITFM-ANTSETTING": Info("Antenna type", enum={0: "Unknown", 1: "Passive", 2: "Active"}),
    "CFG-HW-ANT_CFG_VOLTCTRL": Info("Antenna supply voltage control"),
    "CFG-HW-ANT_CFG_SHORTDET": Info("Antenna short detection"),
    "CFG-HW-ANT_CFG_OPENDET": Info("Antenna open-circuit detection"),
    "CFG-HW-ANT_CFG_PWRDOWN": Info("Power down antenna supply on short"),
    "CFG-HW-ANT_CFG_RECOVER": Info("Automatic recovery from antenna short"),
    # Odometer
    "CFG-ODO-USE_ODO": Info("Enable odometer"),
    "CFG-ODO-USE_COG": Info("Enable low-speed course-over-ground filter"),
    "CFG-ODO-OUTLPVEL": Info("Output low-pass filtered velocity"),
    "CFG-ODO-OUTLPCOG": Info("Output low-pass filtered course over ground"),
    "CFG-ODO-PROFILE": Info("Odometer profile", enum={0: "Running", 1: "Cycling", 2: "Swimming", 3: "Car", 4: "Custom"}),
    # Time pulse
    "CFG-TP-PULSE_DEF": Info("Time pulse definition", enum={0: "Period", 1: "Frequency"}),
    "CFG-TP-PULSE_LENGTH_DEF": Info("Time pulse length definition", enum={0: "Ratio", 1: "Length"}),
    "CFG-TP-TP1_ENA": Info("Enable time pulse 1"),
    "CFG-TP-PERIOD_TP1": Info("Time pulse period", "us"),
    "CFG-TP-PERIOD_LOCK_TP1": Info("Time pulse period when locked to GNSS time", "us"),
    "CFG-TP-FREQ_TP1": Info("Time pulse frequency", "Hz"),
    "CFG-TP-FREQ_LOCK_TP1": Info("Time pulse frequency when locked to GNSS time", "Hz"),
    "CFG-TP-LEN_TP1": Info("Time pulse length", "us"),
    "CFG-TP-LEN_LOCK_TP1": Info("Time pulse length when locked to GNSS time", "us"),
    "CFG-TP-DUTY_TP1": Info("Time pulse duty cycle", "%"),
    "CFG-TP-DUTY_LOCK_TP1": Info("Time pulse duty cycle when locked to GNSS time", "%"),
    "CFG-TP-POL_TP1": Info("Time pulse polarity", enum={0: "Falling edge", 1: "Rising edge"}),
    "CFG-TP-TIMEGRID_TP1": Info("Time pulse time grid", enum=_TIMEREF),
    "CFG-TP-ANT_CABLEDELAY": Info("Antenna cable delay", "ns"),
    "CFG-TP-SYNC_GNSS_TP1": Info("Synchronize time pulse to GNSS time"),
    "CFG-TP-USE_LOCKED_TP1": Info("Use locked parameters when possible"),
    "CFG-TP-ALIGN_TO_TOW_TP1": Info("Align pulse to top of second"),
    # AssistNow Autonomous
    "CFG-ANA-USE_ANA": Info("Enable AssistNow Autonomous"),
    "CFG-ANA-ORBMAXERR": Info("Maximum acceptable modeled orbit error", "m"),
    # SBAS
    "CFG-SBAS-USE_TESTMODE": Info("Use SBAS data in test mode"),
    "CFG-SBAS-USE_RANGING": Info("Use SBAS GEOs as ranging sources"),
    "CFG-SBAS-USE_DIFFCORR": Info("Use SBAS differential corrections"),
    "CFG-SBAS-USE_INTEGRITY": Info("Use SBAS integrity information"),
    "CFG-SBAS-PRNSCANMASK": Info("SBAS PRN search mask (bit 0 = PRN 120)"),
}



def info(name: str) -> Info:
    i = INFO.get(name)
    if i:
        return i
    parts = name.split("-", 2)
    if len(parts) == 3 and parts[1] == "MSGOUT":
        msg, _, port = parts[2].rpartition("_")
        return Info(f"Output rate of {msg.replace('_ID_', ' ').replace('_', '-')} on {port} "
                    "(per navigation solution, 0 = off)")
    if len(parts) == 3 and parts[1] == "INFMSG":
        return Info(f"Information messages enabled on {parts[2].replace('_', ' ')} "
                    "(bits: 0 ERROR, 1 WARNING, 2 NOTICE, 3 TEST, 4 DEBUG)")
    if len(parts) == 3 and parts[1].endswith("INPROT"):
        return Info(f"Accept {parts[2]} input on {parts[1][:-6]}")
    if len(parts) == 3 and parts[1].endswith("OUTPROT"):
        return Info(f"Send {parts[2]} output on {parts[1][:-7]}")
    return Info("")
