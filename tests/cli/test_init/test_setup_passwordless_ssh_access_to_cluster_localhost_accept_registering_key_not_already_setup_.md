Calling setup_passwordless_ssh_access_to_cluster('localhost')
without having setup passwordless SSH access to the cluster beforehand
and the user accepting to register the new public key on the remote
leads to the following commands being executed locally:
- subprocess.run(('ssh-copy-id', '-o', 'StrictHostKeyChecking=no', 'localhost'), stdout=None, stderr=None, capture_output=False, text=True, timeout=None, check=True)
