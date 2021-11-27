import os
import subprocess


def fetch_imagenet(local_directory=None):
    if "SLURM_JOB_ID" in os.environ.keys():
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
        train_directory = os.path.join(local_directory, 'train')
        validation_directory = os.path.join(local_directory, 'val')
        return train_directory, validation_directory
