#!/usr/bin/env python3
"""Generate the editable KiCad schematic pages for HomeKey Lock Rev A.

The electrical net contract is defined in design_data.py.  Each functional
page is a standalone KiCad schematic using global net labels; the PCB is built
from the same manifest, so schematic, BOM, placement and PCB cannot silently
drift apart.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
KIUTILS = WORKSPACE_ROOT / ".tools" / "py"
KICAD_SHARE = WORKSPACE_ROOT / ".tools" / "kicad-root" / "usr" / "share" / "kicad"
EASYEDA = WORKSPACE_ROOT / "tmp" / "easyeda"
ESPRESSIF = WORKSPACE_ROOT / "tmp" / "vendor" / "espressif-kicad"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(KIUTILS))

from design_data import (  # noqa: E402
    ANTENNA,
    DATE,
    NC_PINS,
    PASSIVE_VARIANT_DEFINITIONS,
    PROJECT_NAME,
    REVISION,
    footprint_for,
    parts,
)
from kiutils.footprint import Footprint  # noqa: E402
from kiutils.items.common import (  # noqa: E402
    Effects,
    Font,
    PageSettings,
    Position,
    Property,
    TitleBlock,
)
from kiutils.items.schitems import (  # noqa: E402
    Connection,
    GlobalLabel,
    NoConnect,
    SchematicSymbol,
    SymbolProjectInstance,
    SymbolProjectPath,
    Text,
)
from kiutils.schematic import Schematic  # noqa: E402
from kiutils.symbol import SymbolLib  # noqa: E402


OUT = PROJECT_ROOT / "hardware" / "kicad"
SCHEMATIC_OUT = OUT / "schematics"
LIB_OUT = OUT / "HomeKey_RevA.pretty"


PAGE_TITLES = OrderedDict(
    [
        ("01_POWER_USB", "Power input, USB and always-on rails"),
        ("02_MCU_IO", "ESP32-C6, controls and service I/O"),
        ("03_NFC_CONTROLLER", "PN7161 controller, clock and supplies"),
        ("04_RF_ANTENNA", "13.56 MHz RF matching and PCB antenna"),
        ("05_SERVO_DRIVER", "Switched 5.98 V / 5 A servo supply"),
    ]
)


STOCK_FOOTPRINTS = {
    "R_0603_1608Metric": "Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod",
    "C_0603_1608Metric": "Capacitor_SMD.pretty/C_0603_1608Metric.kicad_mod",
    "C_1206_3216Metric": "Capacitor_SMD.pretty/C_1206_3216Metric.kicad_mod",
    "C_1210_3225Metric": "Capacitor_SMD.pretty/C_1210_3225Metric.kicad_mod",
    "L_0603_1608Metric": "Inductor_SMD.pretty/L_0603_1608Metric.kicad_mod",
    "Crystal_SMD_3225-4Pin_3.2x2.5mm": "Crystal.pretty/Crystal_SMD_3225-4Pin_3.2x2.5mm.kicad_mod",
    "LED_0603_1608Metric": "LED_SMD.pretty/LED_0603_1608Metric.kicad_mod",
    "SW_SPST_TL3305A": "Button_Switch_SMD.pretty/SW_SPST_TL3305A.kicad_mod",
    "TestPoint_Pad_D1.0mm": "TestPoint.pretty/TestPoint_Pad_D1.0mm.kicad_mod",
    "TerminalBlock_1x02_P5.08mm": "TerminalBlock.pretty/TerminalBlock_bornier-2_P5.08mm.kicad_mod",
    "PinHeader_1x03_P2.54mm_Vertical": "Connector_PinHeader_2.54mm.pretty/PinHeader_1x03_P2.54mm_Vertical.kicad_mod",
    "PinHeader_1x02_P2.54mm_Vertical": "Connector_PinHeader_2.54mm.pretty/PinHeader_1x02_P2.54mm_Vertical.kicad_mod",
}


def source_footprint(part) -> Path | None:
    """Return the exact footprint source used for a manifest part."""
    name = part.footprint.split(":", 1)[1]
    if name == ANTENNA.name:
        return None
    if name == "ESP32-C6-MINI-1":
        return ESPRESSIF / "footprints" / "Espressif.pretty" / f"{name}.kicad_mod"
    easyeda = EASYEDA / "HomeKey_RevA.pretty" / f"{name}.kicad_mod"
    if easyeda.exists():
        return easyeda
    if name not in STOCK_FOOTPRINTS:
        raise KeyError(f"No footprint source mapping for {part.ref}: {part.footprint}")
    return KICAD_SHARE / "footprints" / STOCK_FOOTPRINTS[name]


def canonicalize_graphics_only_libraries() -> None:
    """Apply the reviewed non-copper footprint-library batch in place."""

    replacements = {
        "C_1210_3225Metric": [
            ('(fp_text value "C_1210_3225Metric" (at 0 2.3) (layer "F.Fab")',
             '(fp_text value "C_1210_3225Metric" (at 0 2.3) (layer "F.Fab") hide'),
        ],
        "SMB_L4.6-W3.6-LS5.3-RD": [
            ('(fp_text reference REF** (at 0.000 -4.000) (layer F.SilkS)',
             '(fp_text reference REF** (at 0.000 -4.000) (layer F.Fab)'),
            ('(fp_text value SMB_L4.6-W3.6-LS5.3-RD (at 0.000 4.000) (layer F.Fab)',
             '(fp_text value SMB_L4.6-W3.6-LS5.3-RD (at 0.000 4.000) (layer F.Fab) hide'),
        ],
        "SOD-123FL_L2.6-W1.6-LS3.5-R-FD": [
            ('(fp_text reference REF** (at 0.000 -4.000) (layer F.SilkS)',
             '(fp_text reference REF** (at 0.000 -4.000) (layer F.Fab)'),
            ('(fp_text value SOD-123FL_L2.6-W1.6-LS3.5-R-FD (at 0.000 4.000) (layer F.Fab)',
             '(fp_text value SOD-123FL_L2.6-W1.6-LS3.5-R-FD (at 0.000 4.000) (layer F.Fab) hide'),
        ],
        "LED_0603_1608Metric": [
            ('(fp_text reference "REF**" (at 0 -1.43) (layer "F.SilkS")',
             '(fp_text reference "REF**" (at 0 -1.43) (layer "F.Fab")'),
            ('(fp_text value "LED_0603_1608Metric" (at 0 1.43) (layer "F.Fab")',
             '(fp_text value "LED_0603_1608Metric" (at 0 1.43) (layer "F.Fab") hide'),
        ],
        "F1812": [
            ('(fp_text value F1812 (at 0.000 4.000) (layer F.Fab)',
             '(fp_text value F1812 (at 0.000 4.000) (layer F.Fab) hide'),
            ('\t(fp_arc (start -0.00 0.32) (end 0.00 0.63) (angle -180.00) (layer F.SilkS) (width 0.15))\n', ''),
            ('\t(fp_arc (start 0.00 -0.32) (end 0.00 -0.63) (angle -180.00) (layer F.SilkS) (width 0.15))\n', ''),
        ],
        "PinHeader_1x02_P2.54mm_Vertical": [
            ('(fp_text value "PinHeader_1x02_P2.54mm_Vertical" (at 0 4.87) (layer "F.Fab")',
             '(fp_text value "PinHeader_1x02_P2.54mm_Vertical" (at 0 4.87) (layer "F.Fab") hide'),
        ],
        "PinHeader_1x03_P2.54mm_Vertical": [
            ('(fp_text value "PinHeader_1x03_P2.54mm_Vertical" (at 0 7.41) (layer "F.Fab")',
             '(fp_text value "PinHeader_1x03_P2.54mm_Vertical" (at 0 7.41) (layer "F.Fab") hide'),
        ],
        "L_0603_1608Metric": [
            ('(fp_text value "L_0603_1608Metric" (at 0 1.43) (layer "F.Fab")',
             '(fp_text value "L_0603_1608Metric" (at 0 1.43) (layer "F.Fab") hide'),
        ],
        "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL": [
            ('(module easyeda2kicad:SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL (layer F.Cu) (tedit 5DC5F6A4)',
             '(footprint "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL" (version 20240108) (generator pcbnew) (layer F.Cu) (tedit 5DC5F6A4)'),
            ('\t(property "LCSC Part" "C16072")\n', ''),
            ('(fp_text reference REF** (at 0.000 -6.600) (layer F.SilkS)',
             '(fp_text reference REF** (at 0.000 -6.600) (layer F.Fab)'),
            ('(fp_text value SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL (at 0.000 6.600) (layer F.Fab)',
             '(fp_text value SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL (at 0.000 6.600) (layer F.Fab) hide'),
            ('(fp_arc (start -2.53 -0.01) (end -2.53 -0.45) (angle 180.17) (layer F.SilkS) (width 0.15)',
             '(fp_arc (start -2.53 -0.45) (mid -2.09 -0.009347) (end -2.531306 0.429998) (layer F.SilkS) (width 0.15)'),
        ],
    }
    for name, edits in replacements.items():
        path = LIB_OUT / f"{name}.kicad_mod"
        text = path.read_text(encoding="utf-8")
        for before, after in edits:
            if text.count(before) != 1:
                raise RuntimeError(f"{name}: expected exactly one canonicalization match: {before}")
            text = text.replace(before, after)
        path.write_text(text, encoding="utf-8")


def canonicalize_u4_library() -> None:
    """Mirror the reviewed board-edge ESP32 module structure in its library."""

    path = LIB_OUT / "ESP32-C6-MINI-1.kicad_mod"
    text = path.read_text(encoding="utf-8")
    replacements = [
        (
            '(fp_text value "ESP32-C6-MINI-1" (at 0 9.85) (layer "F.Fab")',
            '(fp_text value "ESP32-C6-MINI-1" (at 0 9.85) (layer "F.Fab") hide',
        ),
        (
            '  (fp_line (start -5.925 -8.5) (end -6.775 -8.5)\n'
            '    (stroke (width 0.12) (type solid)) (layer "F.SilkS") (tstamp 01eb9bdd-a337-4673-a2b0-d209aebe8932))\n',
            '',
        ),
        (
            '  (fp_line (start 6.05 -8.5) (end 6.8 -8.5)\n'
            '    (stroke (width 0.12) (type solid)) (layer "F.SilkS") (tstamp e79c0d1b-dd57-46f8-829a-f7a4352235f8))\n',
            '',
        ),
        (
            '  (fp_line (start 6.8 -8.5) (end 6.8 -7.9)\n'
            '    (stroke (width 0.12) (type solid)) (layer "F.SilkS") (tstamp 66154fb2-7455-4e42-b0a7-e0fdebb40a4a))\n',
            '',
        ),
        (
            '(zone (net 0) (net_name "") (layers "*.Cu")',
            '(zone (net 0) (net_name "") (layers "F.Cu" "In1.Cu" "In2.Cu" "B.Cu")',
        ),
        (
            '    (zone_connect 2) (thermal_bridge_angle 45)\n',
            '    (zone_connect 2)\n',
        ),
    ]
    for before, after in replacements:
        if text.count(before) != 1:
            raise RuntimeError(
                "ESP32-C6-MINI-1: expected exactly one U4 canonicalization "
                f"match: {before}"
            )
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8")


def canonicalize_j2_library() -> None:
    """Mirror the reviewed HRO USB-C positioning holes and field visibility."""

    path = LIB_OUT / "USB-C_SMD-TYPE-C-31-M-12_1.kicad_mod"
    text = path.read_text(encoding="utf-8")
    value_before = (
        '(fp_text value USB-C_SMD-TYPE-C-31-M-12_1 (at 0.000 6.474) (layer F.Fab)'
    )
    value_after = value_before + " hide"
    if text.count(value_before) != 1:
        raise RuntimeError("TYPE-C-31-M-12: expected exactly one Value field")
    text = text.replace(value_before, value_after)

    # These two unnumbered 0.60 mm holes receive plastic positioning posts,
    # not solderable shell tabs.  HRO's mechanical intent and independent
    # reviewed KiCad libraries both classify them as non-plated holes.
    peg_before = '(pad "" thru_hole circle'
    peg_after = '(pad "" np_thru_hole circle'
    if text.count(peg_before) != 2:
        raise RuntimeError("TYPE-C-31-M-12: expected exactly two positioning holes")
    text = text.replace(peg_before, peg_after)

    # Match the legacy board writer's canonical spelling for sub-micrometre
    # custom-pad polygon coordinates.  These are numerically identical; using
    # one spelling prevents the library comparator from treating the imported
    # EasyEDA custom pads as different serialized definitions.
    coordinate_spellings = {
        "-0.000076": "-7.6e-05",
        "-0.000051": "-5.1e-05",
        "0.000051": "5.1e-05",
    }
    expected_counts = {"-0.000076": 4, "-0.000051": 4, "0.000051": 4}
    for before, after in coordinate_spellings.items():
        if text.count(before) != expected_counts[before]:
            raise RuntimeError(
                f"TYPE-C-31-M-12: unexpected custom-pad coordinate count for {before}"
            )
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8")


def canonicalize_u5_library() -> None:
    """Apply NXP's SOT618-1 reflow footprint to the PN7161 package.

    The imported EasyEDA copper lands already match NXP (0.29 x 0.90 mm
    perimeter lands and a 4.10 x 4.10 mm exposed pad).  Only the exposed-pad
    stencil was wrong: NXP specifies a 3 x 3 array of 0.60 mm apertures over a
    2.40 mm total span.  Keep copper untouched and canonicalize paste,
    courtyard, field visibility, and the reviewed silk outline.
    """

    path = LIB_OUT / "HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1.kicad_mod"
    text = path.read_text(encoding="utf-8")
    replacements = [
        (
            "(fp_text reference REF** (at 0.000 -7.050) (layer F.SilkS)",
            "(fp_text reference REF** (at 0.000 -7.050) (layer F.Fab)",
        ),
        (
            "(fp_text value HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1 (at 0.000 7.050) (layer F.Fab)",
            "(fp_text value HVQFN-40_L6.0-W6.0-P0.50-BL-EP4.1 (at 0.000 7.050) (layer F.Fab) hide",
        ),
        (
            "\t(fp_arc (start -3.00 3.80) (end -3.00 3.65) (angle 359.03) (layer F.SilkS) (width 0.30))\n",
            "",
        ),
        (
            "\t(pad 41 smd rect (at 0.00 0.00 0.00) (size 4.100 4.100) (layers F.Cu F.Paste F.Mask))",
            "\t(pad 41 smd rect (at 0.00 0.00 0.00) (size 4.100 4.100) (layers F.Cu F.Mask))",
        ),
    ]
    # The pad-41 replacement is intentionally an invariant check: it prevents
    # a vendor-library update from silently restoring full-area F.Paste.
    for before, after in replacements:
        if text.count(before) != 1:
            raise RuntimeError(f"PN7161 SOT618-1: expected exactly one match: {before}")
        text = text.replace(before, after)

    exposed_pad = (
        "\t(pad 41 smd rect (at 0.00 0.00 0.00) (size 4.100 4.100) "
        "(layers F.Cu F.Mask))"
    )
    paste_lines = []
    for y in (-0.90, 0.00, 0.90):
        for x in (-0.90, 0.00, 0.90):
            paste_lines.append(
                f'\t(pad "" smd rect (at {x:5.2f} {y:5.2f}) '
                f'(size 0.600 0.600) (layers F.Paste))'
            )
    if text.count(exposed_pad) != 1:
        raise RuntimeError("PN7161 SOT618-1: canonical exposed pad missing")
    text = text.replace(exposed_pad, exposed_pad + "\n" + "\n".join(paste_lines))

    courtyard_replacements = {
        "(start -3.00 3.00) (end -3.00 -3.00)": "(start -3.625 3.625) (end -3.625 -3.625)",
        "(start -3.00 -3.00) (end 3.00 -3.00)": "(start -3.625 -3.625) (end 3.625 -3.625)",
        "(start 3.00 -3.00) (end 3.00 3.00)": "(start 3.625 -3.625) (end 3.625 3.625)",
        "(start 3.00 3.00) (end -3.00 3.00)": "(start 3.625 3.625) (end -3.625 3.625)",
    }
    for before, after in courtyard_replacements.items():
        if text.count(before) != 1:
            raise RuntimeError(f"PN7161 SOT618-1: missing courtyard edge: {before}")
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8")


def copy_libraries() -> None:
    """Make the KiCad project self-contained and relocatable."""
    OUT.mkdir(parents=True, exist_ok=True)
    SCHEMATIC_OUT.mkdir(parents=True, exist_ok=True)
    LIB_OUT.mkdir(parents=True, exist_ok=True)

    shutil.copy2(EASYEDA / "HomeKey_RevA.kicad_sym", OUT / "HomeKey_RevA.kicad_sym")
    shutil.copy2(
        ESPRESSIF / "symbols" / "Espressif.kicad_sym",
        OUT / "Espressif.kicad_sym",
    )

    copied = set()
    for part in parts:
        source = source_footprint(part)
        if source is None:
            continue
        destination = LIB_OUT / f"{part.footprint.split(':', 1)[1]}.kicad_mod"
        if destination.name in copied:
            continue
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, destination)
        copied.add(destination.name)

    canonicalize_graphics_only_libraries()
    canonicalize_u4_library()
    canonicalize_j2_library()
    canonicalize_u5_library()

    # Keep the six reviewed passive variants project-local.  Their copper,
    # mask, paste, Fab graphics and courtyard are exact copies of the base
    # package; only Value visibility and the reference field layer differ.
    for variant_id, (base_id, reference_layer) in PASSIVE_VARIANT_DEFINITIONS.items():
        variant_name = variant_id.split(":", 1)[1]
        base_name = base_id.split(":", 1)[1]
        footprint = Footprint.from_file(str(LIB_OUT / f"{base_name}.kicad_mod"))
        footprint.entryName = variant_name
        for item in footprint.graphicItems:
            if getattr(item, "type", None) == "reference":
                item.layer = reference_layer
            elif getattr(item, "type", None) == "value":
                item.hide = True
        footprint.to_file(str(LIB_OUT / f"{variant_name}.kicad_mod"))

    # Models referenced by the two larger EasyEDA-derived parts.
    model_out = OUT / "HomeKey_RevA.3dshapes"
    model_out.mkdir(exist_ok=True)
    model_source = EASYEDA / "HomeKey_RevA.3dshapes"
    for name in (
        "CAP-SMD_BD8.0-L8.3-W8.3-FD.step",
        "CAP-SMD_BD8.0-L8.3-W8.3-FD.wrl",
        "IND-SMD_L7.0-W6.6_APH0630.step",
        "IND-SMD_L7.0-W6.6_APH0630.wrl",
        "F1812_L4.5-W3.2-H1.0.step",
        "F1812_L4.5-W3.2-H1.0.wrl",
    ):
        source = model_source / name
        if source.exists():
            shutil.copy2(source, model_out / name)

    (OUT / "sym-lib-table").write_text(
        '(sym_lib_table\n'
        '  (lib (name "HomeKey_RevA")(type "KiCad")(uri "${KIPRJMOD}/HomeKey_RevA.kicad_sym")(options "")(descr "Rev A exact symbols"))\n'
        '  (lib (name "Espressif")(type "KiCad")(uri "${KIPRJMOD}/Espressif.kicad_sym")(options "")(descr "Espressif official symbols"))\n'
        ')\n',
        encoding="utf-8",
    )
    (OUT / "fp-lib-table").write_text(
        '(fp_lib_table\n'
        '  (lib (name "HomeKey_RevA")(type "KiCad")(uri "${KIPRJMOD}/HomeKey_RevA.pretty")(options "")(descr "Rev A self-contained footprints"))\n'
        ')\n',
        encoding="utf-8",
    )


_SYMBOL_LIBRARIES: dict[str, SymbolLib] = {}


def symbol_library(nickname: str) -> SymbolLib:
    if nickname in _SYMBOL_LIBRARIES:
        return _SYMBOL_LIBRARIES[nickname]
    if nickname == "HomeKey_RevA":
        path = EASYEDA / "HomeKey_RevA.kicad_sym"
    elif nickname == "Espressif":
        path = ESPRESSIF / "symbols" / "Espressif.kicad_sym"
    else:
        path = KICAD_SHARE / "symbols" / f"{nickname}.kicad_sym"
    if not path.exists():
        raise FileNotFoundError(path)
    _SYMBOL_LIBRARIES[nickname] = SymbolLib.from_file(str(path))
    return _SYMBOL_LIBRARIES[nickname]


def embedded_symbol(full_name: str):
    nickname, entry_name = full_name.split(":", 1)
    source = next(
        (symbol for symbol in symbol_library(nickname).symbols if symbol.entryName == entry_name),
        None,
    )
    if source is None:
        raise KeyError(f"Symbol {full_name} not found")
    symbol = copy.deepcopy(source)
    symbol.libId = full_name
    return symbol


def symbol_pins(symbol):
    result = []

    def walk(item):
        result.extend(item.pins)
        for child in item.units:
            walk(child)

    walk(symbol)
    return result


def effects(size=1.0, *, hide=False, bold=False):
    return Effects(Font(height=size, width=size, bold=bold), hide=hide)


def outward_wire(origin: Position, relative: Position, length=5.08):
    """Return a point outside a symbol and a readable label angle."""
    if abs(relative.X) >= abs(relative.Y):
        direction = 1 if relative.X >= 0 else -1
        return Position(origin.X + relative.X + direction * length, origin.Y + relative.Y), (0 if direction > 0 else 180)
    direction = 1 if relative.Y >= 0 else -1
    return Position(origin.X + relative.X, origin.Y + relative.Y + direction * length), (90 if direction > 0 else 270)


def add_part(schematic: Schematic, part, origin: Position, embedded: dict[str, object]):
    full_name = part.symbol
    if full_name not in embedded:
        embedded[full_name] = embedded_symbol(full_name)
        schematic.libSymbols.append(embedded[full_name])
    definition = embedded[full_name]

    include_bom = part.fields.get("ExcludeFromBOM") != "yes"
    instance = SchematicSymbol(
        position=origin,
        unit=1,
        inBom=include_bom,
        onBoard=True,
        dnp=part.dnp,
        uuid=str(uuid4()),
    )
    instance.libId = full_name
    instance.properties = [
        Property("Reference", part.ref, 0, Position(origin.X, origin.Y - 7.0, 0), effects(1.0, bold=True)),
        Property("Value", part.value, 1, Position(origin.X, origin.Y + 7.0, 0), effects(0.9)),
        Property("Footprint", footprint_for(part), 2, Position(origin.X, origin.Y, 0), effects(hide=True)),
        Property("Datasheet", "", 3, Position(origin.X, origin.Y, 0), effects(hide=True)),
        Property("LCSC", part.lcsc, 4, Position(origin.X, origin.Y, 0), effects(hide=True)),
        Property("MPN", part.mpn, 5, Position(origin.X, origin.Y, 0), effects(hide=True)),
        Property("Manufacturer", part.manufacturer, 6, Position(origin.X, origin.Y, 0), effects(hide=True)),
        Property("DNP", "yes" if part.dnp else "no", 7, Position(origin.X, origin.Y, 0), effects(hide=True)),
    ]

    pin_definitions = symbol_pins(definition)
    definition_numbers = {str(pin.number) for pin in pin_definitions}
    missing = set(part.pins) - definition_numbers
    if missing:
        raise ValueError(f"{part.ref}: manifest pins absent from symbol: {sorted(missing)}")

    # KiCad requires UUID instance records for all symbol pins, including NCs.
    for pin in pin_definitions:
        instance.pins[str(pin.number)] = str(uuid4())

    nc_numbers = set(NC_PINS.get(part.ref, []))
    connected_points: dict[tuple[float, float, str], bool] = {}
    for pin in pin_definitions:
        number = str(pin.number)
        pin_position = Position(origin.X + pin.position.X, origin.Y + pin.position.Y)
        if number in nc_numbers:
            schematic.noConnects.append(NoConnect(position=pin_position, uuid=str(uuid4())))
            continue
        if number not in part.pins:
            raise ValueError(f"{part.ref} pin {number} is neither connected nor explicitly NC")
        net_name = part.pins[number]
        key = (round(pin_position.X, 5), round(pin_position.Y, 5), net_name)
        if key in connected_points:
            continue
        connected_points[key] = True
        label_position, label_angle = outward_wire(origin, pin.position)
        schematic.graphicalItems.append(
            Connection(
                type="wire",
                points=[pin_position, label_position],
                uuid=str(uuid4()),
            )
        )
        schematic.globalLabels.append(
            GlobalLabel(
                text=net_name,
                shape="passive",
                position=Position(label_position.X, label_position.Y, label_angle),
                effects=effects(0.72),
                uuid=str(uuid4()),
            )
        )

    instance.instances = [
        SymbolProjectInstance(
            name=PROJECT_NAME,
            paths=[
                SymbolProjectPath(
                    sheetInstancePath=f"/{schematic.uuid}",
                    reference=part.ref,
                    unit=1,
                )
            ],
        )
    ]
    schematic.schematicSymbols.append(instance)


def page_positions(page_parts):
    """Place symbols on a generous A2 grid in block order."""
    ordered = sorted(page_parts, key=lambda part: (part.block, part.ref))
    columns = 7
    x0, y0 = 55.0, 68.0
    dx, dy = 77.0, 67.0
    for index, part in enumerate(ordered):
        row, column = divmod(index, columns)
        yield part, Position(x0 + column * dx, y0 + row * dy, 0)


def generate_page(page_key: str, page_title: str) -> Path:
    page_parts = [part for part in parts if part.page == page_key]
    schematic = Schematic.create_new()
    schematic.uuid = str(uuid4())
    schematic.paper = PageSettings("A2")
    schematic.titleBlock = TitleBlock(
        title=f"{PROJECT_NAME} — {page_title}",
        date=DATE,
        revision=REVISION,
        company="DIY engineering prototype",
        comments={
            1: "Connectivity generated from tools/design_data.py",
            2: "RF values are starting values; tune on the installed door",
        },
    )
    schematic.texts.extend(
        [
            Text(
                text=f"{page_key.replace('_', ' ')} — {page_title}",
                position=Position(28, 24, 0),
                effects=effects(2.5, bold=True),
                uuid=str(uuid4()),
            ),
            Text(
                text="GLOBAL LABELS ARE THE ELECTRICAL CONNECTIONS. DNP = assembly option / tuning footprint.",
                position=Position(28, 31, 0),
                effects=effects(1.1),
                uuid=str(uuid4()),
            ),
        ]
    )
    embedded = {}
    for part, position in page_positions(page_parts):
        schematic.texts.append(
            Text(
                text=part.block,
                position=Position(position.X, position.Y - 13.0, 0),
                effects=effects(0.85, bold=True),
                uuid=str(uuid4()),
            )
        )
        add_part(schematic, part, position, embedded)

    path = SCHEMATIC_OUT / f"{page_key}.kicad_sch"
    schematic.to_file(str(path), encoding="utf-8")
    return path


def write_project_file() -> None:
    # Pin the clearance to JLCPCB's standard 4-layer 0.10 mm capability.
    # Leaving this list empty makes KiCad silently assume 0.20 mm and reports
    # hundreds of false-positive violations around the PN7161 fan-out.
    default_netclass = {
        "bus_width": 12,
        "clearance": 0.10,
        "diff_pair_gap": 0.20,
        "diff_pair_via_gap": 0.20,
        "diff_pair_width": 0.20,
        "line_style": 0,
        "microvia_diameter": 0.30,
        "microvia_drill": 0.10,
        "name": "Default",
        "pcb_color": "rgba(0, 0, 0, 0.000)",
        "schematic_color": "rgba(0, 0, 0, 0.000)",
        "track_width": 0.20,
        "via_diameter": 0.50,
        "via_drill": 0.20,
        "wire_width": 6,
    }
    project = {
        "board": {
            "design_settings": {
                "meta": {"version": 2},
                # The In1.Cu GND plane is emitted as a deterministic routed
                # raster so the headless build does not depend on zone-fill
                # state.  KiCad consequently labels every raster end clipped
                # by a mounting-hole or antenna keepout as track_dangling,
                # although the GND net is independently checked as one
                # connected copper component.  Keep real unrouted-net checks
                # enabled and suppress only this non-actionable diagnostic.
                "rule_severities": {"track_dangling": "ignore"},
                "rules": {
                    # Rev A uses 0.50/0.20 mm signal and GND vias.  The
                    # 0.20 mm finished hole is the declared JLCPCB process
                    # minimum for this 1.6 mm four-layer prototype.
                    "min_through_hole_diameter": 0.20,
                },
            },
        },
        "boards": [],
        "cvpcb": {},
        "erc": {},
        "libraries": {},
        "meta": {
            "filename": f"{PROJECT_NAME}.kicad_pro",
            "version": 1,
        },
        "net_settings": {
            "classes": [default_netclass],
            "meta": {"version": 3},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [],
        },
        "pcbnew": {},
        "schematic": {},
        "text_variables": {},
    }
    (OUT / f"{PROJECT_NAME}.kicad_pro").write_text(
        json.dumps(project, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    copy_libraries()
    write_project_file()
    paths = [generate_page(key, title) for key, title in PAGE_TITLES.items()]
    print(f"Generated {len(paths)} KiCad schematic pages in {SCHEMATIC_OUT}")
    for path in paths:
        print(path.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()
