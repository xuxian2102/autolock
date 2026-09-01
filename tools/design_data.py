"""Single source of truth for HomeKey Lock Rev A.

The generators use this manifest for the schematic, PCB, BOM, placement file,
and the human-readable connectivity table.  Pad maps are deliberate: changing
a footprint without reviewing its pad numbering is forbidden.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class Part:
    ref: str
    value: str
    symbol: str
    footprint: str
    pins: Dict[str, str]
    page: str
    block: str
    pcb_at: Tuple[float, float, float]
    lcsc: str = ""
    mpn: str = ""
    manufacturer: str = ""
    dnp: bool = False
    note: str = ""
    source_footprint: str = "stock"
    fields: Dict[str, str] = field(default_factory=dict)


# Project-local footprint names.  The board embeds footprints, but these names
# remain in the schematic and BOM so the project is editable on another PC.
R0603 = "HomeKey_RevA:R_0603_1608Metric"
C0603 = "HomeKey_RevA:C_0603_1608Metric"
C1206 = "HomeKey_RevA:C_1206_3216Metric"
C1210 = "HomeKey_RevA:C_1210_3225Metric"
L0603 = "HomeKey_RevA:L_0603_1608Metric"
LPOWER = "HomeKey_RevA:IND-SMD_L7.0-W6.6_APH0630"
CRYSTAL = "HomeKey_RevA:Crystal_SMD_3225-4Pin_3.2x2.5mm"
LED0603 = "HomeKey_RevA:LED_0603_1608Metric"
SW_SMD = "HomeKey_RevA:SW_SPST_TL3305A"
TP_SMD = "HomeKey_RevA:TestPoint_Pad_D1.0mm"
CONN2 = "HomeKey_RevA:TerminalBlock_1x02_P5.08mm"
HDR3 = "HomeKey_RevA:PinHeader_1x03_P2.54mm_Vertical"


# The first footprint-library synchronization batch is deliberately limited
# to the 58 passive instances identified by FOOTPRINT_SYNC_AUDIT.json.  Do not
# widen these sets to every component using the same package: the other placed
# instances are outside this reviewed batch.
PASSIVE_FAB_REF_REFS = frozenset({
    "C10", "C11", "C12", "C13", "C15", "C16", "C17", "C18", "C19",
    "C20", "C21", "C22", "C23", "C24", "C25", "C27", "C28", "C29",
    "C31", "C32", "C33", "C34", "C35", "C36", "C39", "C40", "C42",
    "C43", "C5", "C7", "C9",
    "R12", "R13", "R14", "R15", "R16", "R17", "R2", "R28", "R29",
    "R3", "R31", "R8", "R9",
})

PASSIVE_HIDDEN_VALUE_REFS = frozenset({
    "C2", "C26", "C3", "C30", "C37", "C38", "C6",
    "R18", "R19", "R20", "R21", "R22", "R23", "R7",
})

PASSIVE_SYNC_REFS = PASSIVE_FAB_REF_REFS | PASSIVE_HIDDEN_VALUE_REFS

# Second synchronization batch: only documentation graphics/fields differ.
# J2 and U4 are resolved by dedicated generator canonicalizers and physical
# delta audits.  U5 is handled by its NXP SOT618-1 package review.
GRAPHICS_SYNC_REFS = frozenset({
    "C1", "D1", "D2", "D5", "F1", "F2", "J3", "J4", "L3", "L4", "Q1",
})

# These placed footprints have been converted from the legacy generator's
# local child-angle convention to KiCad's board-coordinate convention.
NATIVE_CHILD_ANGLE_REFS = PASSIVE_SYNC_REFS | GRAPHICS_SYNC_REFS | frozenset({"J2", "U5"})

PASSIVE_VARIANT_DEFINITIONS = {
    f"{C0603}_FabRef": (C0603, "F.Fab"),
    f"{C0603}_ValueHidden": (C0603, "F.SilkS"),
    f"{R0603}_FabRef": (R0603, "F.Fab"),
    f"{R0603}_ValueHidden": (R0603, "F.SilkS"),
    f"{C1206}_FabRef": (C1206, "F.Fab"),
    f"{C1206}_ValueHidden": (C1206, "F.SilkS"),
}


def footprint_for(part: Part) -> str:
    """Return the reviewed project-library footprint ID for a manifest part."""
    if part.ref in PASSIVE_FAB_REF_REFS:
        return f"{part.footprint}_FabRef"
    if part.ref in PASSIVE_HIDDEN_VALUE_REFS:
        return f"{part.footprint}_ValueHidden"
    return part.footprint


parts = []


def add(ref, value, symbol, footprint, pins, page, block, at,
        lcsc="", mpn="", manufacturer="", dnp=False, note="",
        source_footprint="stock", **fields):
    parts.append(Part(ref, value, symbol, footprint, pins, page, block, at,
                      lcsc, mpn, manufacturer, dnp, note,
                      source_footprint, fields))


def resistor(ref, value, pins, page, block, at, lcsc="", mpn="",
             manufacturer="UNI-ROYAL", dnp=False, note=""):
    add(ref, value, "Device:R_Small", R0603, pins, page, block, at,
        lcsc=lcsc, mpn=mpn, manufacturer=manufacturer, dnp=dnp, note=note)


def capacitor(ref, value, pins, page, block, at, footprint=C0603,
              lcsc="", mpn="", manufacturer="", dnp=False, note=""):
    add(ref, value, "Device:C_Small", footprint, pins, page, block, at,
        lcsc=lcsc, mpn=mpn, manufacturer=manufacturer, dnp=dnp, note=note)


def inductor(ref, value, pins, page, block, at, footprint=LPOWER,
             lcsc="", mpn="", note=""):
    add(ref, value, "Device:L_Small", footprint, pins, page, block, at,
        lcsc=lcsc, mpn=mpn, note=note)


# ---------------------------------------------------------------------------
# Page 1: battery input, USB and always-on logic rails
# ---------------------------------------------------------------------------
add("J1", "BATTERY 3S 9.0-12.6V", "Connector_Generic:Conn_01x02", CONN2,
    {"1": "BAT_RAW", "2": "GND"}, "01_POWER_USB", "Battery input", (78, 69, 0),
    lcsc="C8465", note="5.08 mm screw terminal; verify THT assembly option")
add("F1", "3A 30V PPTC", "Device:Polyfuse", "HomeKey_RevA:F1812",
    {"1": "BAT_RAW", "2": "BAT_FUSED"}, "01_POWER_USB", "Battery input", (79, 62, 0),
    lcsc="C47002391", mpn="SMD1812-300C-30V", manufacturer="BNstar",
    source_footprint="easyeda")
add("D1", "SMBJ15A", "Device:D_TVS", "HomeKey_RevA:SMB_L4.6-W3.6-LS5.3-RD",
    {"1": "BAT_FUSED", "2": "GND"}, "01_POWER_USB", "Battery input", (85, 62, 90),
    lcsc="C320232", mpn="SMBJ15A", source_footprint="easyeda")
add("Q1", "AO4407A", "HomeKey_RevA:AO4407A", "HomeKey_RevA:SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL",
    {"1": "BAT_SYS", "2": "BAT_SYS", "3": "BAT_SYS", "4": "PMOS_GATE",
     "5": "BAT_FUSED", "6": "BAT_FUSED", "7": "BAT_FUSED", "8": "BAT_FUSED"},
    "01_POWER_USB", "Battery input", (91, 62, 0), lcsc="C16072", mpn="AO4407A",
    manufacturer="AOS", source_footprint="easyeda")
resistor("R1", "100k", {"1": "PMOS_GATE", "2": "GND"}, "01_POWER_USB", "Battery input", (89, 57, 90), lcsc="C25803")
add("D2", "10V Zener", "Device:D_Zener", "HomeKey_RevA:SOD-123FL_L2.6-W1.6-LS3.5-R-FD",
    {"1": "BAT_SYS", "2": "PMOS_GATE"}, "01_POWER_USB", "Battery input", (93, 57, 90),
    lcsc="C209597", mpn="KDZVTR10B", source_footprint="easyeda")
capacitor("C1", "22uF 25V", {"1": "BAT_SYS", "2": "GND"}, "01_POWER_USB", "Battery input", (97, 63, 90), footprint=C1210, lcsc="C515687")
capacitor("C2", "100nF 50V", {"1": "BAT_SYS", "2": "GND"}, "01_POWER_USB", "Battery input", (97, 58, 90), lcsc="C1591")

add("U1", "AP63205WU-7 5V", "HomeKey_RevA:AP63205WU-7", "HomeKey_RevA:TSOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL",
    {"1": "5V_BAT", "2": "BAT_SYS", "3": "BAT_SYS", "4": "GND", "5": "SW_5V", "6": "BST_5V"},
    "01_POWER_USB", "5V logic buck", (103, 61, 0), lcsc="C2071056", mpn="AP63205WU-7",
    manufacturer="Diodes Inc.", source_footprint="easyeda")
capacitor("C3", "10uF 25V", {"1": "BAT_SYS", "2": "GND"}, "01_POWER_USB", "5V logic buck", (101, 56, 90), footprint=C1206,
          lcsc="C9807", mpn="CL31A106KAHNNNE", manufacturer="Samsung Electro-Mechanics")
capacitor("C4", "100nF", {"1": "BST_5V", "2": "SW_5V"}, "01_POWER_USB", "5V logic buck", (106, 56, 0), lcsc="C1591")
inductor("L1", "4.7uH 6A", {"1": "SW_5V", "2": "5V_BAT"}, "01_POWER_USB", "5V logic buck", (109, 61, 0), lcsc="C5349705", mpn="APH0630T4R7M")
capacitor("C5", "22uF 10V", {"1": "5V_BAT", "2": "GND"}, "01_POWER_USB", "5V logic buck", (114, 59, 90), footprint=C1206, lcsc="C5672")
capacitor("C6", "22uF 10V", {"1": "5V_BAT", "2": "GND"}, "01_POWER_USB", "5V logic buck", (117, 59, 90), footprint=C1206, lcsc="C5672")
add("D3", "B5819W", "HomeKey_RevA:B5819W_C8598", "HomeKey_RevA:SOD-123_L2.7-W1.6-LS3.7-RD-1",
    {"2": "5V_BAT", "1": "SYS_5V"}, "01_POWER_USB", "5V source OR", (121, 61, 0),
    lcsc="C8598", mpn="B5819W", source_footprint="easyeda")

add("J2", "USB-C USB2", "HomeKey_RevA:TYPE-C-31-M-12", "HomeKey_RevA:USB-C_SMD-TYPE-C-31-M-12_1",
    {"A1B12": "GND", "B1A12": "GND", "A4B9": "USB_5V", "B4A9": "USB_5V",
     "A5": "USB_CC1", "B5": "USB_CC2", "A6": "USB_DP_CONN", "B6": "USB_DP_CONN",
     "A7": "USB_DN_CONN", "B7": "USB_DN_CONN",
     "1": "GND", "2": "GND", "3": "GND", "4": "GND"},
    "01_POWER_USB", "USB-C", (129, 3, 180), lcsc="C165948", mpn="TYPE-C-31-M-12",
    manufacturer="HRO", source_footprint="easyeda")
resistor("R2", "5.1k", {"1": "USB_CC1", "2": "GND"}, "01_POWER_USB", "USB-C", (122, 9, 90), lcsc="C2907044")
resistor("R3", "5.1k", {"1": "USB_CC2", "2": "GND"}, "01_POWER_USB", "USB-C", (126, 9, 90), lcsc="C2907044")
add("U2", "USBLC6-2SC6", "HomeKey_RevA:USBLC6-2SC6_C5261088", "HomeKey_RevA:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR",
    {"1": "USB_DN_CONN", "6": "USB_DN_CONN", "3": "USB_DP_CONN", "4": "USB_DP_CONN",
     "2": "GND", "5": "USB_5V"}, "01_POWER_USB", "USB-C", (119, 14, 0),
    lcsc="C5261088", mpn="USBLC6-2SC6", source_footprint="easyeda")
resistor("R4", "22R", {"1": "USB_DN_CONN", "2": "USB_DM"}, "01_POWER_USB", "USB-C", (112, 12, 0),
         lcsc="C23345", mpn="0603WAF220JT5E")
resistor("R5", "22R", {"1": "USB_DP_CONN", "2": "USB_DP"}, "01_POWER_USB", "USB-C", (112, 16, 0),
         lcsc="C23345", mpn="0603WAF220JT5E")
add("D4", "B5819W", "HomeKey_RevA:B5819W_C8598", "HomeKey_RevA:SOD-123_L2.7-W1.6-LS3.7-RD-1",
    {"2": "USB_5V", "1": "SYS_5V"}, "01_POWER_USB", "5V source OR", (116, 22, 0),
    lcsc="C8598", mpn="B5819W", source_footprint="easyeda")

add("U3", "AP63203WU-7 3V3", "HomeKey_RevA:AP63203WU-7", "HomeKey_RevA:TSOT-26_L2.9-W1.6-P0.95-LS2.8-BL",
    {"1": "3V3", "2": "SYS_5V", "3": "SYS_5V", "4": "GND", "5": "SW_3V3", "6": "BST_3V3"},
    "01_POWER_USB", "3V3 buck", (112, 37, 0), lcsc="C780769", mpn="AP63203WU-7",
    manufacturer="Diodes Inc.", source_footprint="easyeda")
capacitor("C7", "10uF 10V", {"1": "SYS_5V", "2": "GND"}, "01_POWER_USB", "3V3 buck", (107, 36, 90), footprint=C1206,
          lcsc="C9807", mpn="CL31A106KAHNNNE", manufacturer="Samsung Electro-Mechanics")
capacitor("C8", "100nF", {"1": "BST_3V3", "2": "SW_3V3"}, "01_POWER_USB", "3V3 buck", (113, 32, 0), lcsc="C1591")
inductor("L2", "4.7uH 6A", {"1": "SW_3V3", "2": "3V3"}, "01_POWER_USB", "3V3 buck", (119, 37, 0), lcsc="C5349705", mpn="APH0630T4R7M")
capacitor("C9", "22uF 10V", {"1": "3V3", "2": "GND"}, "01_POWER_USB", "3V3 buck", (125, 35, 90), footprint=C1206, lcsc="C5672")
capacitor("C10", "22uF 10V", {"1": "3V3", "2": "GND"}, "01_POWER_USB", "3V3 buck", (128, 35, 90), footprint=C1206, lcsc="C5672")

# ---------------------------------------------------------------------------
# Page 2: ESP32-C6, user controls and sensing
# ---------------------------------------------------------------------------
esp_pins = {str(n): "GND" for n in [1, 2, 11, 14, *range(36, 54)]}
esp_pins.update({"3": "3V3", "5": "BAT_ADC", "6": "SERVICE_BTN", "8": "ESP_EN",
                 "12": "PN_IRQ", "13": "PN_VEN", "17": "USB_DM", "18": "USB_DP",
                 "19": "STATUS_LED", "23": "BOOT", "24": "PN_NSS", "25": "PN_SCK",
                 "26": "PN_MOSI", "27": "PN_MISO", "28": "SERVO_PWM", "29": "SERVO_EN",
                 "30": "UART_RX", "31": "UART_TX"})
add("U4", "ESP32-C6-MINI-1-N4", "Espressif:ESP32-C6-MINI-1/U", "HomeKey_RevA:ESP32-C6-MINI-1",
    esp_pins, "02_MCU_IO", "ESP32-C6", (94, 8.5, 0), lcsc="C5736265", mpn="ESP32-C6-MINI-1-N4",
    manufacturer="Espressif", source_footprint="espressif")
capacitor("C11", "10uF 10V", {"1": "3V3", "2": "GND"}, "02_MCU_IO", "ESP32-C6", (86, 19, 90), footprint=C1206,
          lcsc="C9807", mpn="CL31A106KAHNNNE", manufacturer="Samsung Electro-Mechanics")
capacitor("C12", "100nF", {"1": "3V3", "2": "GND"}, "02_MCU_IO", "ESP32-C6", (90, 19, 90), lcsc="C1591")
resistor("R6", "10k", {"1": "3V3", "2": "ESP_EN"}, "02_MCU_IO", "Reset and boot", (98, 20, 90), lcsc="C25804")
capacitor("C13", "1uF", {"1": "ESP_EN", "2": "GND"}, "02_MCU_IO", "Reset and boot", (102, 20, 90), lcsc="C15849")
add("SW1", "RESET", "Switch:SW_Push", SW_SMD, {"1": "ESP_EN", "2": "GND"},
    "02_MCU_IO", "Reset and boot", (107, 20, 0), lcsc="C2886899",
    mpn="TL3305AF160QG", manufacturer="E-Switch")
resistor("R7", "10k", {"1": "3V3", "2": "BOOT"}, "02_MCU_IO", "Reset and boot", (112, 20, 90), lcsc="C25804")
add("SW2", "BOOT", "Switch:SW_Push", SW_SMD, {"1": "BOOT", "2": "GND"},
    "02_MCU_IO", "Reset and boot", (117, 20, 0), lcsc="C2886899",
    mpn="TL3305AF160QG", manufacturer="E-Switch")
resistor("R8", "10k", {"1": "3V3", "2": "SERVICE_BTN"}, "02_MCU_IO", "Service", (123, 20, 90), lcsc="C25804")
add("SW3", "SERVICE", "Switch:SW_Push", SW_SMD, {"1": "SERVICE_BTN", "2": "GND"},
    "02_MCU_IO", "Service", (128, 20, 0), lcsc="C2886899",
    mpn="TL3305AF160QG", manufacturer="E-Switch")
add("D5", "Green LED", "Device:LED_Small", LED0603, {"1": "GND", "2": "LED_A"},
    "02_MCU_IO", "Status", (133, 20, 270), lcsc="C12624", mpn="KT-0603G",
    manufacturer="Hubei KENTO Elec",
    note="Verified standard polarity: pin 1=K to GND, pin 2=A to R9; rotate footprint 180 degrees in place")
# D5 is a 525 nm emerald green part: Vf is 3.1 V at 5 mA, so a 3.3 V GPIO leaves
# only ~0.2 V across R9 and the current is set by the difference of two similar
# numbers.  At 1k that came to 0.1-0.5 mA against a 5 mA rating -- a 4:1 spread
# across production Vf, with high-Vf units not lighting at all.  100R gives
# 1.4-2.1 mA, which always lights and stays well inside the rating (1.3 mW).
# Do not raise this back toward 1k unless D5 is changed to a lower-Vf part.
resistor("R9", "100R", {"1": "STATUS_LED", "2": "LED_A"}, "02_MCU_IO", "Status", (133, 15, 90),
         lcsc="C22775", mpn="0603WAF1000T5E")
resistor("R10", "1M", {"1": "BAT_SYS", "2": "BAT_ADC"}, "02_MCU_IO", "Battery ADC", (74, 52, 90), lcsc="C105578")
resistor("R11", "220k", {"1": "BAT_ADC", "2": "GND"}, "02_MCU_IO", "Battery ADC", (74, 58, 90),
         lcsc="C22961", mpn="0603WAF2203T5E")
capacitor("C14", "100nF", {"1": "BAT_ADC", "2": "GND"}, "02_MCU_IO", "Battery ADC", (78, 58, 90), lcsc="C1591")

# ---------------------------------------------------------------------------
# Page 3: PN7161 controller and its local supplies/clock
# ---------------------------------------------------------------------------
pn_pins = {"1": "PN_NSS", "2": "PN_DWL_REQ", "3": "PN_MOSI", "4": "GND", "5": "PN_MISO",
           "6": "3V3", "7": "PN_SCK", "8": "PN_IRQ", "9": "GND", "10": "PN_VEN",
           "12": "SYS_5V", "13": "SYS_5V", "14": "PN_TVDD", "15": "PN_RXN", "16": "PN_RXP",
           "17": "PN_VMID", "18": "PN_TVDD", "19": "PN_TX2", "20": "GND", "21": "PN_TX1",
           "22": "PN_TVDD", "26": "PN_VDD", "27": "PN_VDD", "28": "SYS_5V", "29": "XTAL2",
           "30": "XTAL1", "31": "PN_VDD", "41": "GND"}
add("U5", "PN7161B1HN/C100E", "HomeKey_RevA:PN7161B1HN_C100E", "HomeKey_RevA:HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1",
    pn_pins, "03_NFC_CONTROLLER", "PN7161", (61, 37, 0), lcsc="C3303780", mpn="PN7161B1HN/C100E",
    manufacturer="NXP", source_footprint="easyeda",
    note="Pins 11,23-25,32-40 NC; DCDC_EN is an output and is intentionally NC")
add("X1", "27.12MHz 10pF", "Device:Crystal_GND24_Small", CRYSTAL,
    {"1": "XTAL1", "3": "XTAL2", "2": "GND", "4": "GND"},
    "03_NFC_CONTROLLER", "Clock", (70, 46, 0), lcsc="C90919", mpn="7M27100009", manufacturer="TXC")
capacitor("C15", "10pF C0G", {"1": "XTAL1", "2": "GND"}, "03_NFC_CONTROLLER", "Clock", (67, 50, 90), lcsc="C1634")
capacitor("C16", "10pF C0G", {"1": "XTAL2", "2": "GND"}, "03_NFC_CONTROLLER", "Clock", (73, 50, 90), lcsc="C1634")
resistor("R12", "10k", {"1": "PN_DWL_REQ", "2": "GND"}, "03_NFC_CONTROLLER", "Host interface", (64, 28, 90), lcsc="C25804")
resistor("R13", "100k", {"1": "PN_VEN", "2": "GND"}, "03_NFC_CONTROLLER", "Host interface", (67, 28, 90), lcsc="C25803")
for i, net in enumerate(["PN_NSS", "PN_SCK", "PN_MOSI", "PN_MISO"], start=14):
    resistor(f"R{i}", "0R", {"1": net, "2": net + "_IC"}, "03_NFC_CONTROLLER", "Host interface", (53 + (i-14)*2.5, 29, 90), lcsc="C21189")
# Correct the IC-side pin nets after the series resistors.
pn_pins["1"] = "PN_NSS_IC"; pn_pins["7"] = "PN_SCK_IC"; pn_pins["3"] = "PN_MOSI_IC"; pn_pins["5"] = "PN_MISO_IC"
next(p for p in parts if p.ref == "U5").pins.update(pn_pins)

capacitor("C17", "100nF", {"1": "SYS_5V", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 supply", (66, 34, 90), lcsc="C1591")
capacitor("C18", "4.7uF 10V", {"1": "SYS_5V", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 supply", (69, 34, 90), lcsc="C1705")
capacitor("C19", "1uF 10V", {"1": "3V3", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 supply", (54, 34, 90), lcsc="C15849")
capacitor("C20", "4.7uF 10V", {"1": "SYS_5V", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 supply", (72, 34, 90), lcsc="C1705")
capacitor("C21", "2.2uF 10V", {"1": "PN_TVDD", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 TX supply", (55, 43, 90), lcsc="C1607")
capacitor("C22", "2.2uF 10V", {"1": "PN_TVDD", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 TX supply", (58, 46, 90), lcsc="C1607")
capacitor("C23", "100nF", {"1": "PN_VDD", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 core supply", (64, 46, 90), lcsc="C1591")
capacitor("C24", "2.2uF 10V", {"1": "PN_VDD", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 core supply", (64, 49, 90), lcsc="C1607")
capacitor("C25", "2.2uF 10V", {"1": "PN_VDD", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 core supply", (64, 52, 90), lcsc="C1607")
capacitor("C26", "100nF", {"1": "PN_VMID", "2": "GND"}, "03_NFC_CONTROLLER", "PN7161 VMID", (58, 41, 90), lcsc="C1591")

# ---------------------------------------------------------------------------
# Page 4: RF matching and the board antenna
# ---------------------------------------------------------------------------
inductor("L3", "160nH 5% RF", {"1": "PN_TX1", "2": "RF_P0"}, "04_RF_ANTENNA", "EMC filter", (52, 34, 0), footprint=L0603, lcsc="C437367", mpn="LQW18CNR16J00D")
inductor("L4", "160nH 5% RF", {"1": "PN_TX2", "2": "RF_N0"}, "04_RF_ANTENNA", "EMC filter", (52, 40, 0), footprint=L0603, lcsc="C437367", mpn="LQW18CNR16J00D")
capacitor("C27", "330pF C0G 2%", {"1": "RF_P0", "2": "GND"}, "04_RF_ANTENNA", "EMC filter", (49, 31, 90),
          lcsc="C882521", mpn="GRM1885C1H331FA01D", manufacturer="Murata Electronics")
capacitor("C28", "330pF C0G 2%", {"1": "RF_N0", "2": "GND"}, "04_RF_ANTENNA", "EMC filter", (49, 43, 90),
          lcsc="C882521", mpn="GRM1885C1H331FA01D", manufacturer="Murata Electronics")
capacitor("C29", "68pF C0G 2%", {"1": "RF_P0", "2": "RF_P1"}, "04_RF_ANTENNA", "Matching", (48, 35.5, 0),
          lcsc="C237335", mpn="GRM1885C1H680FA01D", manufacturer="Murata Electronics")
capacitor("C30", "68pF C0G 2%", {"1": "RF_N0", "2": "RF_N1"}, "04_RF_ANTENNA", "Matching", (48, 38.5, 0),
          lcsc="C237335", mpn="GRM1885C1H680FA01D", manufacturer="Murata Electronics")
capacitor("C31", "100pF C0G 2%", {"1": "RF_P1", "2": "GND"}, "04_RF_ANTENNA", "Matching", (45, 32, 90), lcsc="C5360849")
capacitor("C32", "100pF C0G 2%", {"1": "RF_N1", "2": "GND"}, "04_RF_ANTENNA", "Matching", (45, 43, 90), lcsc="C5360849")
capacitor("C33", "DNP trim", {"1": "RF_P0", "2": "RF_P1"}, "04_RF_ANTENNA", "Matching", (48, 33, 0), dnp=True)
capacitor("C34", "DNP trim", {"1": "RF_N0", "2": "RF_N1"}, "04_RF_ANTENNA", "Matching", (48, 41, 0), dnp=True)
capacitor("C35", "DNP trim", {"1": "RF_P1", "2": "GND"}, "04_RF_ANTENNA", "Matching", (42, 32, 90), dnp=True)
capacitor("C36", "DNP trim", {"1": "RF_N1", "2": "GND"}, "04_RF_ANTENNA", "Matching", (42, 43, 90), dnp=True)
capacitor("C37", "1nF C0G", {"1": "PN_RXP", "2": "RXP_AC"}, "04_RF_ANTENNA", "Receiver tap", (57, 31, 0), lcsc="C163508")
capacitor("C38", "1nF C0G", {"1": "PN_RXN", "2": "RXN_AC"}, "04_RF_ANTENNA", "Receiver tap", (57, 44, 0), lcsc="C163508")
resistor("R18", "2.7R 1%", {"1": "RF_P1", "2": "RF_P2"}, "04_RF_ANTENNA", "Q damping", (43, 35.5, 0),
         lcsc="C22946", mpn="0603WAF270KT5E")
resistor("R19", "2.7R 1%", {"1": "RF_N1", "2": "RF_N2"}, "04_RF_ANTENNA", "Q damping", (43, 38.5, 0),
         lcsc="C22946", mpn="0603WAF270KT5E")
resistor("R20", "2.2k 1%", {"1": "RXP_AC", "2": "RF_P0"}, "04_RF_ANTENNA", "Receiver tap", (53, 31, 0),
         lcsc="C4190", mpn="0603WAF2201T5E")
resistor("R21", "2.2k 1%", {"1": "RXN_AC", "2": "RF_N0"}, "04_RF_ANTENNA", "Receiver tap", (53, 44, 0),
         lcsc="C4190", mpn="0603WAF2201T5E")
resistor("R22", "0R ANT ON", {"1": "RF_P2", "2": "ANT_P"}, "04_RF_ANTENNA", "Antenna select", (49, 35.5, 0), lcsc="C21189")
resistor("R23", "0R ANT ON", {"1": "RF_N2", "2": "ANT_N"}, "04_RF_ANTENNA", "Antenna select", (49, 38.5, 0), lcsc="C21189")
resistor("R24", "DNP EXT", {"1": "RF_P2", "2": "EXT_ANT_P"}, "04_RF_ANTENNA", "Antenna select", (50, 46, 0), lcsc="C21189", dnp=True)
resistor("R25", "DNP EXT", {"1": "RF_N2", "2": "EXT_ANT_N"}, "04_RF_ANTENNA", "Antenna select", (50, 49, 0), lcsc="C21189", dnp=True)
add("J3", "EXT ANT DNP", "Connector_Generic:Conn_01x02", "HomeKey_RevA:PinHeader_1x02_P2.54mm_Vertical",
    {"1": "EXT_ANT_P", "2": "EXT_ANT_N"}, "04_RF_ANTENNA", "Antenna select", (55, 48, 90), dnp=True,
    note="Unpopulated fallback pads; never fit at the same time as R22/R23 without retuning")
add("AE1", "40x40mm 4-turn PCB antenna", "Device:Antenna_Loop", "HomeKey_RevA:NFC_Antenna_40x40_4T",
    {"1": "ANT_N", "2": "ANT_P"}, "04_RF_ANTENNA", "PCB antenna", (48, 36, 0), dnp=False,
    note="Board-defined copper; excluded from BOM and placement",
    ExcludeFromBOM="yes", ExcludeFromPosition="yes")

# ---------------------------------------------------------------------------
# Page 5: switched 5.94 V servo rail
# ---------------------------------------------------------------------------
add("U6", "TPS565201DDCR", "HomeKey_RevA:TPS565201DDCR", "HomeKey_RevA:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR",
    {"1": "GND", "2": "SW_SERVO", "3": "BAT_SYS", "4": "SERVO_FB", "5": "SERVO_EN_IC", "6": "BST_SERVO"},
    "05_SERVO_DRIVER", "Servo buck", (116, 58, 0), lcsc="C327676", mpn="TPS565201DDCR",
    manufacturer="Texas Instruments", source_footprint="easyeda")
capacitor("C39", "10uF 25V", {"1": "BAT_SYS", "2": "GND"}, "05_SERVO_DRIVER", "Servo buck", (111, 57, 90), footprint=C1206,
          lcsc="C9807", mpn="CL31A106KAHNNNE", manufacturer="Samsung Electro-Mechanics")
capacitor("C40", "100nF 50V", {"1": "BAT_SYS", "2": "GND"}, "05_SERVO_DRIVER", "Servo buck", (111, 61, 90), lcsc="C1591")
resistor("R26", "10k", {"1": "SERVO_EN", "2": "SERVO_EN_IC"}, "05_SERVO_DRIVER", "Servo enable", (112, 52, 0), lcsc="C25804")
resistor("R27", "100k", {"1": "SERVO_EN_IC", "2": "GND"}, "05_SERVO_DRIVER", "Servo enable", (116, 52, 90), lcsc="C25803")
capacitor("C41", "100nF", {"1": "BST_SERVO", "2": "SW_SERVO"}, "05_SERVO_DRIVER", "Servo buck", (119, 53, 0), lcsc="C1591")
inductor("L5", "4.7uH 6A", {"1": "SW_SERVO", "2": "SERVO_6V"}, "05_SERVO_DRIVER", "Servo buck", (123, 58, 0), lcsc="C5349705", mpn="APH0630T4R7M")
resistor("R28", "68.1k 1%", {"1": "SERVO_6V", "2": "SERVO_FB"}, "05_SERVO_DRIVER", "Feedback", (126, 53, 90), lcsc="C185315")
resistor("R29", "10k 1%", {"1": "SERVO_FB", "2": "GND"}, "05_SERVO_DRIVER", "Feedback", (130, 53, 90), lcsc="C25804")
capacitor("C42", "47uF 10V", {"1": "SERVO_6V", "2": "GND"}, "05_SERVO_DRIVER", "Servo output", (129, 60, 90), footprint=C1206, lcsc="C96123")
capacitor("C43", "47uF 10V", {"1": "SERVO_6V", "2": "GND"}, "05_SERVO_DRIVER", "Servo output", (133, 60, 90), footprint=C1206, lcsc="C96123")
capacitor("C44", "1000uF 10V", {"1": "SERVO_6V", "2": "GND"}, "05_SERVO_DRIVER", "Servo output", (139, 58, 0), footprint="HomeKey_RevA:CAP-SMD_BD8.0-L8.3-W8.3-FD", lcsc="C311227")
capacitor("C45", "22pF DNP", {"1": "SERVO_6V", "2": "SERVO_FB"}, "05_SERVO_DRIVER", "Feedback", (128, 50, 0), dnp=True)
resistor("R30", "220R", {"1": "SERVO_PWM", "2": "SERVO_PWM_OUT"}, "05_SERVO_DRIVER", "Servo signal", (136, 50, 0),
         lcsc="C22962", mpn="0603WAF2200T5E")
resistor("R31", "10k", {"1": "SERVO_PWM_OUT", "2": "GND"}, "05_SERVO_DRIVER", "Servo signal", (140, 50, 90), lcsc="C25804")
add("F2", "2.5A 16V PPTC", "Device:Polyfuse", "HomeKey_RevA:F1812",
    {"1": "SERVO_6V", "2": "SERVO_6V_OUT"}, "05_SERVO_DRIVER", "Servo output", (143, 64, 0),
    lcsc="C210838", mpn="MF-MSMF250/16X-2", manufacturer="Bourns",
    source_footprint="easyeda", note="Cable/servo short-circuit protection; not a stall cutoff")
add("J4", "DS3115 SERVO", "Connector_Generic:Conn_01x03", HDR3,
    {"1": "GND", "2": "SERVO_6V_OUT", "3": "SERVO_PWM_OUT"}, "05_SERVO_DRIVER", "Servo output", (147, 60, 90),
    lcsc="C124375", note="Standard 2.54 mm 3-pin header; GND / +6V / PWM")


# Test pads are board-level aids.  They are included in the schematic but not BOM.
for idx, (net, at) in enumerate([
    ("BAT_SYS", (77, 54, 0)), ("5V_BAT", (120, 56, 0)), ("SYS_5V", (122, 27, 0)),
    ("3V3", (132, 35, 0)), ("SERVO_6V_OUT", (145, 67, 0)), ("GND", (88, 69, 0)),
    ("PN_IRQ", (76, 28, 0)), ("PN_VEN", (79, 28, 0)), ("PN_DWL_REQ", (82, 28, 0)),
    ("UART_TX", (105, 27, 0)), ("UART_RX", (108, 27, 0)), ("BAT_ADC", (81, 56, 0)),
], start=1):
    add(f"TP{idx}", net, "Connector:TestPoint", TP_SMD, {"1": net}, "02_MCU_IO", "Test points", at,
        dnp=True, note="Copper test pad; no component", ExcludeFromBOM="yes")


# Explicit no-connect pads.  They still exist in the footprint but must not be
# assigned a copper net.  The schematic generator adds no-connect markers.
NC_PINS = {
    "U4": ["4", "7", "9", "10", "15", "16", "20", "21", "22", "32", "33", "34", "35"],
    "U5": ["11", "23", "24", "25", "32", "33", "34", "35", "36", "37", "38", "39", "40"],
    "J2": ["A8", "B8"],
}


BOARD_ONLY = [
    # ref, x, y, drill, diameter
    ("H1", 4, 4, 3.2, 6.0), ("H2", 146, 4, 3.2, 6.0),
    ("H3", 4, 71, 3.2, 6.0), ("H4", 146, 71, 3.2, 6.0),
]


BOARD_SIZE = (150.0, 75.0)
PROJECT_NAME = "HomeKey-Lock-RevA-PN7161"
REVISION = "A"
DATE = "2026-08-27"


def part_by_ref(ref):
    return next(p for p in parts if p.ref == ref)


def validate_manifest():
    refs = [p.ref for p in parts]
    assert len(refs) == len(set(refs)), "duplicate reference designator"
    assert part_by_ref("U5").pins["1"] == "PN_NSS_IC"
    assert part_by_ref("U4").pins["12"] == "PN_IRQ"
    assert part_by_ref("J4").pins == {"1": "GND", "2": "SERVO_6V_OUT", "3": "SERVO_PWM_OUT"}


validate_manifest()
