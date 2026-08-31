# Autolock

Rev A NFC engineering artifacts now include a reusable 13.56 MHz magnetic
near-field reduced-order model.

- [Rev A NFC link report](reports/NFC_LINK_MODEL_REV_A.md)
- [Model usage and calibration guide](tools/nfc_model/README.md)

Quick start:

```bash
python -m pip install -e '.[report]'
python -m nfc_model simulate \
  --door zetland-solid-core --door-thickness 40 \
  --lock-distance 10 --ferrite 0.5 \
  --phone-gap 2 --phone-offset 10 --phone-angle 10
```
