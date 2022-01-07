import os
import socket

running_on_mila_cluster = socket.getfqdn().endswith(".server.mila.quebec") and "SLURM_TMPDIR" in os.environ.keys()
