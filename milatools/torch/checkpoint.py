import os
import logging
import tempfile

import torch

import milatools.torch.distributed as dist

log = logging.getLogger()


class Checkpoint:
    """Checkpointer that saves all the object it is given periodically
    Checkpointer works for any python objects and for pytorch objects.
    It also handle saving & loading objects that were saved from different cuda devices


    Attributes
    ----------
    root: str
        Path where checkpoints should be saved

    name: str
        Name of the checkpoint of uniquely identify them

    every: int
        Save period, 2 means a checkpoint will be created every 2 epochs

    kwargs:
        Dictionnary of object to keep track of

    """

    def __init__(self, root, name, every=2, **kwargs):
        self.data = kwargs
        self.every = every
        self.root = root
        self.name = name

    def end_epoch(self, epoch):
        """Called when the epoch finishes. Used to determined if we should save a new checkpoint or not"""
        if epoch % self.every > 0:
            return

        self.save()

    def _load(self):
        # this is made to make it work with a single GPU
        # with 2 processes on a single GPU
        # for testing purposes
        map_location = {"cuda:%d" % 0: "cuda:%d" % dist.device_id()}

        log.info("Loading checkpoint")
        state_dict = torch.load(
            self.path,
            map_location=map_location,
        )

        for key, value in self.data.items():
            if key not in state_dict:
                continue

            state = state_dict[key]

            if hasattr(value, "load_state_dict"):
                value.load_state_dict(state)
            else:
                # Update regular python object
                value.__dict__.update(state.__dict__)

    @property
    def path(self):
        return os.path.join(self.root, self.name + ".chkpt")

    def load(self):
        """Make all workers load the same initial state"""

        if not os.path.exists(self.path):
            # Save a checkpoint if none, so workers can load something
            log.info("No checkpoint found")
            self.save()
            dist.barrier()  # Make workers load the new save
            dist.barrier()  # Wait for workers to load the save
            return

        # wait for rank 0 to save the checkpoint
        dist.barrier()

        # Load the checkpoint
        #  - if the checkpoint existed everybody loads it
        #  - if the checkpoint did not exist only the workers load it
        self._load()

        # wait for everybody to load the checkpoint
        dist.barrier()

    def save(self):
        """Save the current state of the trained to make it resumable"""
        log.info("save checkpoint")

        # only rank 0 can save the model
        if not dist.has_weight_autority():
            return

        state_dict = dict()

        for key, value in self.data.items():
            if hasattr(value, "state_dict"):
                state_dict[key] = value.state_dict()
            else:
                state_dict[key] = value

        os.makedirs(self.root, exist_ok=True)

        # Save to a temporary file and then move it into the final file,
        # this is to prevent writing bad checkpoint  as move is atomic;
        # in case of a failure the last good checkpoint is not going
        # to be corrupted
        _, name = tempfile.mkstemp(dir=os.path.dirname(self.path))

        torch.save(state_dict, name)

        # name and path need to be on the same filesystem on POSIX
        # (mv requirement)
        os.replace(name, self.path)
