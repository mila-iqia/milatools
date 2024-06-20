Calling setup_passwordless_ssh_access_to_cluster('localhost')
with passwordless SSH access to localhost already setup
and the user accepting to register the new public key on the remote
leads to the following commands being executed locally:
- subprocess.run(('ssh', '-O', 'check', '-oControlPath=~/.cache/ssh/<USER>@localhost:2222', 'localhost'), shell=False, text=True, capture_output=True)
