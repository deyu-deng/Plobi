# Pico 2W HID firmware (host contract)

Host speaks **newline-delimited JSON** over USB serial (default 115200).

## Requests

```json
{"op": "ping"}
{"op": "action", "type": "type_text", "payload": {"text": "hello"}}
{"op": "action", "type": "key", "payload": {"key": "enter"}}
{"op": "action", "type": "hotkey", "payload": {"keys": ["ctrl", "l"]}}
{"op": "action", "type": "move", "payload": {"x": 100, "y": 200}}
{"op": "action", "type": "click", "payload": {"button": "left"}}
{"op": "action", "type": "wait", "payload": {"ms": 200}}
```

## Responses

```json
{"ok": true, "mode": "usb_hid"}
{"ok": false, "error": "..."}
```

Flash MicroPython / CircuitPython HID keyboard+mouse on the Pico 2W, implement the
loop above, then set on the host:

```
PLOBI_PICO_SERIAL=COM5   # or /dev/ttyACM0
PLOBI_PICO_BAUD=115200
```

Until then, `plobi_hid_run` defaults to mock-safe execution.
