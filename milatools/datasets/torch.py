import os
import subprocess
from typing import Tuple

import torchvision.datasets

import milatools.utils

DATASET_PATH = "/network/datasets/{}.var/{}_torchvision"


def fetch_imagenet(local_directory=None):
    if milatools.running_on_mila_cluster:
        dataset_home = os.path.join(os.environ["SLURM_TMPDIR"], "ImageNet")
        train_directory = os.path.join(dataset_home, "train")
        validation_directory = os.path.join(dataset_home, "val")

        subprocess.run(f"mkdir -p {train_directory}/ {validation_directory}/")
        subprocess.run(f"tar -xf /network/datasets/imagenet/ILSVRC2012_img_train.tar -C {train_directory}")

        p = subprocess.Popen(['cp', '-r', f'/network/datasets/imagenet.var/imagenet_torchvision/val {dataset_home}/'])
        subprocess.run(
            'find ' + train_directory + ' -name "*.tar" | while read NAME ; do mkdir -p "${NAME%.tar}"; tar -xf "${NAME}" -C "${NAME%.tar}"; rm -f "${NAME}"; done', shell=True
        )
        p.wait()
        return train_directory, validation_directory
    else:
        if local_directory is None:
            raise ValueError("Please specify a local directory to store the dataset")

        train_directory = os.path.join(local_directory, 'train')
        validation_directory = os.path.join(local_directory, 'val')
        return train_directory, validation_directory


def fetch_cifar100():
    raise NotImplementedError("CIFAR100 is not available in milatools, yet")


def fetch_cifar10():
    raise NotImplementedError("CIFAR10 is not available in milatools, yet")


# if "SLURM_JOB_ID" in os.environ.keys():


class MNIST(torchvision.datasets.MNIST):
    def __init__(self, local_directory: str = None, *dataset_args, **dataset_kwargs):
        if milatools.running_on_mila_cluster:
            local_directory = os.path.join(os.environ["SLURM_TMPDIR"], "MNIST")
            mnist_path = DATASET_PATH.format("mnist", "mnist")
            subprocess.run(f"mkdir -p {local_directory}/")
            subprocess.run(f"tar -xf {mnist_path} -C {mnist_path}")
        super().__init__(local_directory, *dataset_args, **dataset_kwargs)


def fetch_dataset(dataset_name, local_directory=None, *dataset_args, **dataset_kwargs) -> Tuple[str, str]:
    if dataset_name == 'imagenet':
        return fetch_imagenet(local_directory)
    elif dataset_name == 'cifar10':
        return fetch_cifar10()
    elif dataset_name == 'cifar100':
        return fetch_cifar100()
    elif dataset_name == 'mnist':
        raise ValueError("Use MNIST(local_directory=...) instead")
