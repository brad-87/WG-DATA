import paramiko, time

class CollectData():

    def __init__(self, ssh_dict, wg_dict, peer_dict):
        self.host = ssh_dict.get("host", "127.0.0.1")
        self.port = int( ssh_dict.get("port", "22") )
        self.user = ssh_dict.get("user", "root")
        self.key_file = ssh_dict.get("key_file", "keys/root_rsa")

        self.command = wg_dict.get("command")
        self.poll_int = wg_dict.get("poll_seconds", 2400)
        self.active_int = wg_dict.get("active_seconds", 600)

        self.disp_output = wg_dict.get("display_output", False)
        self.peer_names = peer_dict

    def run_command(self):
        # Start the SSH connection
        ssh = paramiko.SSHClient()
        ssh.load_system_host_keys()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=self.host, port=self.port, username=self.user,key_filename=self.key_file,timeout=10)

        try:
            # Run the command, capture output, exit code, and any error data
            stdin, stdout, stderr = ssh.exec_command(self.command, timeout=10)
            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

            # Check for clean command exec
            if exit_code !=0:
                raise RuntimeError(f"Command failed with exit code {exit_code}: {error.strip()}")
            if error.strip():
                print(f"Warning from server: {error.strip()}")
            
            # Parse the command output
            output = self.parse(output)



            if self.disp_output:
                print("\nWG_OUTPUT: True")
                print("|              [peer_name]               |             [endpoint_ip]              |                  [tx]                  |                  [rx]                  |                [state]")

                for line in output:
                    for entry in line:
                        print(f"|{line.get(entry):^40}", end="")
                        #print(f"|{entry:^40}", end="")
                    print("")
                print("\nEND\n")



            if output:
                print(f"Info ({time.time()}): Data Aquired")
                return output
            else:
                return 0
        
        finally:
            ssh.close()
    
    def parse(self, wg_data):
        data = []
        output = []
        now = int(time.time())

        # WireGuard peer row:
        # 0 interface
        # 1 public-key
        # 2 preshared-key
        # 3 endpoint
        # 4 allowed-ips
        # 5 latest-handshake
        # 6 rx-bytes
        # 7 tx-bytes
        # 8 persistent-keepalive

        for line in wg_data.splitlines():
            entry = line.split("\t")

            if len(entry) == 5:
                continue

            if len(entry) == 9:
                data.append(entry)
            else:
                print(f"Warning: (Parsing) Ignoring row -> data row has wrong shape (size:{len(entry)})({entry})")

        if not data:
            print("Warning: No data could be parsed. Ignoring this collection cycle")
            return []

        for entry in data:
            endpoint_raw = str(entry[3])
            allowed_ips = str(entry[4])
            latest_handshake = int(entry[5])
            rx_bytes = int(entry[6])
            tx_bytes = int(entry[7])

            # Get primary VPN IP from allowed IPs
            # Example: "172.16.255.5/32,10.10.10.0/24"
            vpn_ip = allowed_ips.split(",")[0].strip().split("/")[0]

            # Look up friendly name from config
            name = self.peer_names.get(vpn_ip, f"UNKNOWN-{vpn_ip}")

            # Work out endpoint host only
            if endpoint_raw == "(none)":
                endpoint = "none"
            else:
                endpoint = endpoint_raw.rsplit(":", 1)[0]

            # Work out state
            if latest_handshake == 0:
                last_handshake_seconds = None
                active = False
                state = "Never"
            else:
                last_handshake_seconds = now - latest_handshake
                active = last_handshake_seconds <= self.active_int
                state = "Connected" if active else "Disconnected"

            # Optional group hint for MQTT/dashboard
            if name.startswith("SITE"):
                group = "site"
            elif name.startswith("PEER"):
                group = "peer"
            else:
                group = "unknown"

            output.append({
                "name": name,
                "group": group,
                "vpn_ip": vpn_ip,
                "endpoint": endpoint,
                "tx_mb": round(tx_bytes / 1024**2, 2),
                "rx_mb": round(rx_bytes / 1024**2, 2),
                "state": state,
                "active": active,
                "last_handshake_seconds": last_handshake_seconds,
            })

        return output