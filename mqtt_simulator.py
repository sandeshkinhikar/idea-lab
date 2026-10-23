"""Optional MQTT sensor publisher/subscriber; every emitted record is simulated."""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

TOPIC_TEMPLATE = "nagpur/wards/{ward_id}/sensors"


def _parse_broker(url: str) -> tuple[str, int, bool, str | None, str | None]:
    parsed = urlparse(url if "://" in url else f"mqtt://{url}")
    if parsed.scheme not in {"mqtt", "mqtts", "tcp", "ssl"} or not parsed.hostname:
        raise ValueError("Broker must be mqtt://host[:port] or mqtts://host[:port].")
    secure = parsed.scheme in {"mqtts", "ssl"}
    port = parsed.port or (8883 if secure else 1883)
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    return parsed.hostname, port, secure, username, password


def build_simulated_payload(ward_id: str, *, rng: random.Random | None = None) -> dict[str, Any]:
    """Make a clearly labeled synthetic reading (not evidence of physical IoT)."""
    generator = rng or random.Random()
    return {
        "ward_id": str(ward_id),
        "device_id": f"SIM-{ward_id}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "temperature": round(generator.uniform(30.0, 43.0), 1),
        "humidity": round(generator.uniform(30.0, 75.0), 1),
        "quality": "simulated",
        "simulated": True,
        "provenance": "simulated_mqtt",
        "physical_node_connected": False,
    }


def ingest_mqtt_message(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a subscribed topic/payload and persist it through the alert pipeline."""
    parts = str(topic).split("/")
    if len(parts) != 4 or parts[0] != "nagpur" or parts[1] != "wards" or parts[3] != "sensors" or not parts[2] or parts[2] in {"+", "#"}:
        raise ValueError("Expected topic nagpur/wards/{ward_id}/sensors.")
    ward_id = parts[2]
    payload_ward = str(payload.get("ward_id", ward_id))
    if payload_ward != ward_id:
        raise ValueError("MQTT topic ward_id and payload ward_id do not match.")
    quality = str(payload.get("quality") or "mqtt_unverified")
    source = str(payload.get("provenance") or "")
    simulated = bool(payload.get("simulated")) or quality.lower() in {"simulated", "demo"} or any(tag in source.lower() for tag in ("demo", "synthetic", "simulat"))
    timestamp = payload.get("timestamp") or payload.get("ts")
    if timestamp:
        try:
            datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("MQTT timestamp must be ISO-8601.") from exc
    from backend.routes.alerts import ingest_sensor_reading
    return ingest_sensor_reading(
        ward_id=ward_id,
        device_id=str(payload.get("device_id") or f"MQTT-{ward_id}"),
        temperature=float(payload.get("temperature")),
        humidity=float(payload.get("humidity")),
        quality="simulated" if simulated else quality,
        provenance="demo_synthetic" if simulated else "source_configured",
        timestamp=str(timestamp) if timestamp else None,
    )


def _new_client(mqtt: Any, client_id: str) -> Any:
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id)


def run_simulator(*, broker_url: str, ward_id: str = "DEMO-01", interval: float = 2.0, count: int = 0, subscribe: bool = False, simulate: bool = False) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError("paho-mqtt is not installed. Install it with `pip install paho-mqtt`. No physical sensor connection is assumed.") from exc
    host, port, secure, username, password = _parse_broker(broker_url)
    client = _new_client(mqtt, f"geocool-simulator-{os.getpid()}")
    if username is not None:
        client.username_pw_set(username, password)
    if secure:
        client.tls_set()

    topic_filter = TOPIC_TEMPLATE.format(ward_id="+")
    def on_connect(client_obj: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        try:
            failed = bool(getattr(reason_code, "is_failure", False))
            code = int(reason_code)
        except (TypeError, ValueError):
            failed, code = False, reason_code
        if failed or code not in (0, None):
            print(f"MQTT broker connection failed: {reason_code}", file=sys.stderr, flush=True)
            return
        print(f"Connected to MQTT broker {host}:{port}; this does not indicate a physical node is connected.", flush=True)
        if subscribe:
            client_obj.subscribe(topic_filter, qos=1)
            print(f"Subscribed to {topic_filter}", flush=True)

    def on_message(client_obj: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MQTT payload must be a JSON object.")
            result = ingest_mqtt_message(message.topic, payload)
            print(json.dumps({"topic": message.topic, "result": result, "received_at": datetime.now(timezone.utc).isoformat(), "physical_node_verified": False}), flush=True)
        except Exception as exc:
            print(json.dumps({"topic": message.topic, "error": str(exc), "received_at": datetime.now(timezone.utc).isoformat()}), file=sys.stderr, flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.loop_start()
    try:
        if simulate:
            topic = TOPIC_TEMPLATE.format(ward_id=ward_id)
            published = 0
            while count <= 0 or published < count:
                payload = build_simulated_payload(ward_id)
                info = client.publish(topic, json.dumps(payload), qos=1, retain=False)
                info.wait_for_publish(timeout=10)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(f"MQTT publish failed with return code {info.rc}.")
                print(json.dumps({"topic": topic, "payload": payload, "published": True}), flush=True)
                published += 1
                if count <= 0 or published < count:
                    time.sleep(max(0.1, interval))
        elif subscribe:
            while True:
                time.sleep(1.0)
        else:
            raise ValueError("Select --simulate to publish simulated readings or --subscribe to listen.")
    except KeyboardInterrupt:
        print("Stopping MQTT simulator.", flush=True)
    finally:
        client.loop_stop()
        client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish simulated GeoCool ward sensor records or listen to the sensor topic.")
    parser.add_argument("--broker", default=os.getenv("MQTT_BROKER_URL", "mqtt://localhost:1883"), help="MQTT URL (default from MQTT_BROKER_URL or mqtt://localhost:1883)")
    parser.add_argument("--ward-id", default="DEMO-01", help="Ward segment of nagpur/wards/{ward_id}/sensors")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between simulated readings")
    parser.add_argument("--count", type=int, default=0, help="Number to publish; 0 publishes until interrupted")
    parser.add_argument("--simulate", action="store_true", help="Publish synthetic payloads explicitly marked simulated")
    parser.add_argument("--subscribe", action="store_true", help="Subscribe to ward sensor topics (can be combined with --simulate)")
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.count < 0:
        parser.error("--interval must be positive and --count cannot be negative")
    if not args.simulate and not args.subscribe:
        parser.error("choose --simulate and/or --subscribe")
    try:
        run_simulator(broker_url=args.broker, ward_id=args.ward_id, interval=args.interval, count=args.count, subscribe=args.subscribe, simulate=args.simulate)
    except Exception as exc:
        print(f"MQTT simulator error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
