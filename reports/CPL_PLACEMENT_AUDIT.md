# CPL placement audit

- Result: **PASS**
- CPL rows: `93/93` expected top-side SMT instances
- Centroids vs PCB footprint anchors: `PASS`
- Rotations vs PCB footprint rotations: `PASS`
- Layer: `PASS - all Top`
- D5: `270 deg`, pin 1/K = `GND`, pin 2/A = `LED_A`

## Critical placements

| Ref | LCSC | X mm | Y mm | Rotation | Pin 1 net(s) |
|---|---|---:|---:|---:|---|
| D1 | C320232 | 103.500 | 62.000 | 90.0 deg | BAT_FUSED |
| D2 | C209597 | 110.000 | 62.000 | 90.0 deg | BAT_SYS |
| D3 | C8598 | 121.000 | 45.500 | 0.0 deg | SYS_5V |
| D4 | C8598 | 122.000 | 28.500 | 0.0 deg | SYS_5V |
| D5 | C12624 | 145.000 | 21.000 | 270.0 deg | GND |
| Q1 | C16072 | 108.000 | 68.000 | 0.0 deg | BAT_SYS |
| C44 | C311227 | 136.000 | 65.000 | 0.0 deg | SERVO_6V |
| U1 | C2071056 | 100.000 | 53.500 | 0.0 deg | 5V_BAT |
| U2 | C5261088 | 122.000 | 11.000 | 0.0 deg | USB_DN_CONN |
| U3 | C780769 | 100.000 | 35.500 | 0.0 deg | 3V3 |
| U4 | C5736265 | 99.000 | 8.500 | 0.0 deg | GND |
| U5 | C3303780 | 78.500 | 37.500 | 180.0 deg | PN_NSS_IC |
| U6 | C327676 | 128.000 | 50.500 | 0.0 deg | GND |
| J2 | C165948 | 134.000 | 5.300 | 180.0 deg | GND |
| X1 | C90919 | 79.000 | 48.000 | 0.0 deg | XTAL1 |
| SW1 | C2886899 | 113.000 | 22.000 | 0.0 deg | ESP_EN |
| SW2 | C2886899 | 124.000 | 22.000 | 0.0 deg | BOOT |
| SW3 | C2886899 | 136.000 | 22.000 | 0.0 deg | SERVICE_BTN |

## Remaining limitation

This audit proves that the CPL exactly represents the reviewed KiCad footprint anchors and rotations. It cannot prove how JLCPCB's selected library model defines 0 degrees. The online PCBA preview must therefore be checked for the critical placements above before payment.
