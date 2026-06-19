# Class: config_loader -> Responsible be reading the file config.yaml and returning the contents for parsing

from pathlib import Path
import yaml

class GetConfig():
    
    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path)
        self.config = self.read_config()

    def read_config(self):
        # Ensure file exists
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with self.config_path.open("r", encoding="utf-8") as f:
            print(f"Config file read : {self.config_path}")
            return yaml.safe_load(f)


    def get(self):
        # Return config contents
        
        for heading, contents in self.config.items():
            if heading == "ssh":
                r_ssh = contents

            elif heading == "mqtt":
                r_mqtt = contents

            elif heading == "wireguard":
                r_wireguard = contents

            elif heading == "peer_names":
                r_peer_names = contents

            else:
                print(f"Config Warning: Ignoring unrelated section - [ {heading} ]")

        return r_ssh, r_mqtt, r_wireguard, r_peer_names
    
    def disp_config(self):
        # For debug pruposes while creating the program
        print(f"Config headings in {self.config_path}")

        for heading, contents in self.config.items():
            print(f"\n[ {heading} ]")
            for key,val in contents.items():
                print(f"\tⱶ-→ {key} : {val}")