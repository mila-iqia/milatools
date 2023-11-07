import json
from pathlib import Path
from socket import timeout


def set_remote_ssh_vscode_settings(
    vscode_settings_json_path: Path,
    timeout_seconds: int,
    fully_qualified_node_name: str,
) -> None:
    # TODO: If on Windows, would need to add this to the settings.json file at this path
    # C:\Users\<username>\AppData\Roaming\Code\User\settings.json
    # settings_j
    with open(vscode_settings_json_path) as f:
        settings_json = json.load(f)
    settings_json.setdefault("remote.SSH.connectTimeout", timeout_seconds)
    remote_platform = settings_json.get("remote.SSH.remotePlatform", {})
    remote_platform.setdefault("fully_qualified_node_name", "linux")
    settings_json["remote.SSH.remotePlatform"] = remote_platform

    with open(vscode_settings_json_path, "w") as f:
        json.dump(settings_json, f, indent=4)
