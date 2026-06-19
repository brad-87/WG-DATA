import json
import re
import time

import paho.mqtt.client as mqtt


class MqttClient:
    """
    MQTT publisher for WireGuard peer monitoring.

    Main ideas:

    Discovery messages:
        Tell Home Assistant what entities to create.

    State messages:
        Send live peer data every polling cycle.

    This version separates peers into groups:
        site
        peer
        unknown
    """

    def __init__(self, mqtt_dict):
        self.enabled = mqtt_dict.get("enabled", False)

        self.host = mqtt_dict.get("host", "127.0.0.1")
        self.port = int(mqtt_dict.get("port", 1883))
        self.username = mqtt_dict.get("username")
        self.password = mqtt_dict.get("password")

        self.client_id = mqtt_dict.get("client_id", "wgmonitor-v2")

        # Live data root
        self.topic_prefix = mqtt_dict.get("topic_prefix", "wgmonitor_v2")

        # Home Assistant discovery root
        self.discovery_enabled = mqtt_dict.get("discovery_enabled", True)
        self.discovery_prefix = mqtt_dict.get("discovery_prefix", "homeassistant")
        self.discovery_node = mqtt_dict.get("discovery_node", "wgmonitor_v2")
        self.discovery_retain = mqtt_dict.get("discovery_retain", True)

        self.retain_state = mqtt_dict.get("retain_state", False)
        self.preview_payloads = mqtt_dict.get("preview_payloads", False)

        self.availability_topic = f"{self.topic_prefix}/status"

        self.client = mqtt.Client(client_id=self.client_id)

    # -------------------------
    # Connection handling
    # -------------------------

    def connect(self):
        if not self.enabled:
            print("MQTT disabled")
            return

        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)

        # If the program crashes, broker publishes "offline"
        self.client.will_set(
            self.availability_topic,
            payload="offline",
            retain=True
        )

        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()

        self.client.publish(
            self.availability_topic,
            payload="online",
            retain=True
        )

        print(f"MQTT connected: {self.host}:{self.port}")

    def disconnect(self):
        if not self.enabled:
            return

        self.client.publish(
            self.availability_topic,
            payload="offline",
            retain=True
        )

        time.sleep(0.2)

        self.client.loop_stop()
        self.client.disconnect()

        print("MQTT disconnected")

    # -------------------------
    # Name / group handling
    # -------------------------

    def make_slug(self, name):
        """
        Convert a human name into a safe MQTT/Home Assistant slug.

        Examples:
            SITE-CBM.RACK      -> site_cbm_rack
            PEER.Brad-Phone    -> peer_brad_phone
        """

        slug = str(name).lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = re.sub(r"_+", "_", slug)
        slug = slug.strip("_")

        return slug

    def detect_group(self, name, peer=None):
        """
        Work out whether this is a site, peer, or unknown.

        It checks:
            1. peer["group"] if using dictionary data
            2. name prefix, such as SITE-CBM.RACK or PEER.Brad-Phone
        """

        if isinstance(peer, dict):
            group = peer.get("group") or peer.get("type") or peer.get("kind")

            if group:
                group = str(group).lower()

                if group in ["site", "sites"]:
                    return "site"

                if group in ["peer", "peers", "user", "users"]:
                    return "peer"

        slug = self.make_slug(name)

        if slug.startswith("site_"):
            return "site"

        if slug.startswith("peer_"):
            return "peer"

        return "unknown"

    def ensure_grouped_slug(self, name, group):
        """
        Make sure the slug includes site_ or peer_ at the start.
        """

        slug = self.make_slug(name)

        if group == "site" and not slug.startswith("site_"):
            slug = f"site_{slug}"

        elif group == "peer" and not slug.startswith("peer_"):
            slug = f"peer_{slug}"

        return slug

    # -------------------------
    # Data conversion
    # -------------------------

    def to_float(self, value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def normalise_peer(self, peer):
        """
        Convert your peer data into one consistent dictionary.

        Supports your current list format:
            [name, endpoint, tx_mb, rx_mb, state]

        Also supports future dictionary format:
            {
                "name": "...",
                "endpoint": "...",
                "tx_mb": 123.4,
                "rx_mb": 456.7,
                "state": "Connected",
                "vpn_ip": "172.16.255.5",
                "group": "site"
            }
        """

        if isinstance(peer, dict):
            name = peer.get("name", "UNKNOWN")
            group = self.detect_group(name, peer)
            slug = peer.get("slug") or self.ensure_grouped_slug(name, group)

            state = peer.get("state", "Unknown")
            active = peer.get("active")

            if active is None:
                active = str(state).lower() in ["connected", "active", "online"]

            return {
                "name": name,
                "slug": slug,
                "group": group,

                "vpn_ip": peer.get("vpn_ip", "unknown"),
                "endpoint": peer.get("endpoint", "unknown"),

                "rx_mb": self.to_float(peer.get("rx_mb", 0)),
                "tx_mb": self.to_float(peer.get("tx_mb", 0)),

                "state": state,
                "active": bool(active),

                "last_handshake_seconds": peer.get("last_handshake_seconds"),
                "updated_at": int(time.time()),
            }

        if isinstance(peer, list):
            name = peer[0] if len(peer) > 0 else "UNKNOWN"
            endpoint = peer[1] if len(peer) > 1 else "unknown"
            tx_mb = peer[2] if len(peer) > 2 else 0
            rx_mb = peer[3] if len(peer) > 3 else 0
            state = peer[4] if len(peer) > 4 else "Unknown"

            group = self.detect_group(name)
            slug = self.ensure_grouped_slug(name, group)

            return {
                "name": name,
                "slug": slug,
                "group": group,

                "vpn_ip": "unknown",
                "endpoint": endpoint,

                "rx_mb": self.to_float(rx_mb),
                "tx_mb": self.to_float(tx_mb),

                "state": state,
                "active": str(state).lower() in ["connected", "active", "online"],

                "last_handshake_seconds": None,
                "updated_at": int(time.time()),
            }

        raise TypeError(f"Unsupported peer data type: {type(peer)}")

    # -------------------------
    # Topic builders
    # -------------------------

    def state_topic(self, peer):
        """
        Example:
            wgmonitor_v2/peers/site/site_cbm_rack/state
            wgmonitor_v2/peers/peer/peer_brad_phone/state
        """

        return (
            f"{self.topic_prefix}/peers/"
            f"{peer['group']}/"
            f"{peer['slug']}/state"
        )

    def discovery_topic(self, component, object_id):
        """
        Example:
            homeassistant/sensor/wgmonitor_v2/site_cbm_rack_rx_mb/config
            homeassistant/binary_sensor/wgmonitor_v2/site_cbm_rack_active/config
        """

        return (
            f"{self.discovery_prefix}/"
            f"{component}/"
            f"{self.discovery_node}/"
            f"{object_id}/config"
        )

    # -------------------------
    # Low-level publishing
    # -------------------------

    def publish_json(self, topic, payload, retain=False):
        if not self.enabled:
            return

        payload_json = json.dumps(payload)

        if self.preview_payloads:
            print()
            print(f"MQTT topic: {topic}")
            print(f"MQTT payload: {payload_json}")

        return self.client.publish(
            topic,
            payload=payload_json,
            retain=retain
        )

    # -------------------------
    # State publishing
    # -------------------------

    def publish_state(self, peer):
        peer = self.normalise_peer(peer)

        topic = self.state_topic(peer)

        self.publish_json(
            topic,
            peer,
            retain=self.retain_state
        )

    def publish_peer(self, peer):
        """
        Alias, because publish_peer sounds nicer.
        """

        self.publish_state(peer)

    def publish_all_states(self, peers):
        if not self.enabled:
            return

        for peer in peers:
            self.publish_state(peer)

    # -------------------------
    # Home Assistant discovery
    # -------------------------

    def device_info(self):
        return {
            "identifiers": [self.discovery_node],
            "name": "WireGuard Monitor",
            "manufacturer": "Custom",
            "model": "Python WireGuard Monitor"
        }

    def publish_discovery(self, peers):
        if not self.enabled:
            return

        if not self.discovery_enabled:
            print("MQTT discovery disabled")
            return

        for peer in peers:
            peer = self.normalise_peer(peer)

            self.publish_active_discovery(peer)

            self.publish_sensor_discovery(
                peer,
                field="state",
                suffix="status",
                display_suffix="Status",
                icon="mdi:vpn"
            )

            self.publish_sensor_discovery(
                peer,
                field="group",
                suffix="group",
                display_suffix="Group",
                icon="mdi:folder-network"
            )

            self.publish_sensor_discovery(
                peer,
                field="vpn_ip",
                suffix="vpn_ip",
                display_suffix="VPN IP",
                icon="mdi:ip-network"
            )

            self.publish_sensor_discovery(
                peer,
                field="endpoint",
                suffix="endpoint",
                display_suffix="Endpoint",
                icon="mdi:wan"
            )

            self.publish_sensor_discovery(
                peer,
                field="rx_mb",
                suffix="rx_mb",
                display_suffix="RX",
                unit="MB",
                icon="mdi:download-network"
            )

            self.publish_sensor_discovery(
                peer,
                field="tx_mb",
                suffix="tx_mb",
                display_suffix="TX",
                unit="MB",
                icon="mdi:upload-network"
            )

            self.publish_sensor_discovery(
                peer,
                field="last_handshake_seconds",
                suffix="last_handshake_seconds",
                display_suffix="Last Handshake",
                unit="s",
                icon="mdi:timer-outline"
            )

        print("MQTT discovery published")

    def publish_active_discovery(self, peer):
        slug = peer["slug"]
        name = peer["name"]

        object_id = f"{slug}_active"
        topic = self.discovery_topic("binary_sensor", object_id)
        state_topic = self.state_topic(peer)

        payload = {
            "name": f"{name} Active",
            "object_id": f"{self.discovery_node}_{object_id}",
            "unique_id": f"{self.discovery_node}_{object_id}",

            "state_topic": state_topic,
            "value_template": "{% if value_json.active %}ON{% else %}OFF{% endif %}",
            "payload_on": "ON",
            "payload_off": "OFF",

            "json_attributes_topic": state_topic,

            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",

            "device": self.device_info()
        }

        self.publish_json(
            topic,
            payload,
            retain=self.discovery_retain
        )

    def publish_sensor_discovery(
        self,
        peer,
        field,
        suffix,
        display_suffix,
        unit=None,
        icon=None
    ):
        slug = peer["slug"]
        name = peer["name"]

        object_id = f"{slug}_{suffix}"
        topic = self.discovery_topic("sensor", object_id)
        state_topic = self.state_topic(peer)

        payload = {
            "name": f"{name} {display_suffix}",
            "object_id": f"{self.discovery_node}_{object_id}",
            "unique_id": f"{self.discovery_node}_{object_id}",

            "state_topic": state_topic,
            "value_template": "{{ value_json." + field + " }}",

            "json_attributes_topic": state_topic,

            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",

            "device": self.device_info()
        }

        if unit:
            payload["unit_of_measurement"] = unit

        if icon:
            payload["icon"] = icon

        self.publish_json(
            topic,
            payload,
            retain=self.discovery_retain
        )