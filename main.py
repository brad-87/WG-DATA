from class_files.config_loader import GetConfig
from class_files.data_collector import CollectData
from class_files.mqtt_client import MqttClient
import threading, time

stop_event = threading.Event()
active_timer = None


def request_stop(signum=None, frame=None):
    print("Stop requested")
    stop_event.set()

    global active_timer
    if active_timer is not None:
        
        active_timer.cancel()


def timer_callback(mqtt_client, collector, poll_seconds):
    global active_timer

    if stop_event.is_set():
        return

    try:
        data = collector.run_command()
        mqtt_client.publish_all_states(data)

    except Exception as e:
        print(f"Polling error: {e}")

    finally:
        if not stop_event.is_set():
            active_timer = threading.Timer(poll_seconds, timer_callback, args=(mqtt_client, collector, poll_seconds))
            active_timer.start()

def main():
    global active_timer

    # Read config file
    config = GetConfig("config.yaml")
    if not config:
        print("Error: No data returned from config reader")
        return

    # Split up config data into sections
    d_ssh, d_mqtt, d_wireguard, d_peer_names = config.get()

    collector = CollectData(d_ssh, d_wireguard, d_peer_names)
    mqtt_client = MqttClient(d_mqtt)

    poll_seconds = d_wireguard.get("poll_seconds", 120)

    mqtt_client.connect()

    try:
        # First data collection
        data = collector.run_command()

        if not data:
            print("Error: No data returned from collector")
            return

        # Send Home Assistant discovery while MQTT is connected
        mqtt_client.publish_discovery(data)

        # Send first state packet while MQTT is connected
        mqtt_client.publish_all_states(data)

        # Start polling timer
        active_timer = threading.Timer(
            poll_seconds,
            timer_callback,
            args=(mqtt_client, collector, poll_seconds)
        )
        active_timer.start()

        # Keep program alive
        while not stop_event.is_set():
            stop_event.wait(1)

    finally:
        print("Shutting down safely...")

        stop_event.set()

        if active_timer is not None:
            active_timer.cancel()

        mqtt_client.disconnect()

        print("Shutdown complete")
    

if __name__ == "__main__":
    main()