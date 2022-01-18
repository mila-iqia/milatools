import torch
import torchvision
import torchvision.transforms as transforms
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

import matplotlib.pyplot as plt
import numpy as np


import milatools.torch.distributed as dist

PATH = "./cifar_net.pth"

transform = transforms.Compose(
    [
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    ]
)


class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = torch.flatten(x, 1)  # flatten all dimensions except batch
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


@dist.record
def train():
    trainset = torchvision.datasets.CIFAR10(
        root="./data",
        train=True,
        download=dist.has_dataset_autority(),
        transform=transform,
    )

    # Wait for the main worker to finish downloading the dataset
    dist.barrier()

    trainloader = torch.utils.data.DataLoader(
        trainset,
        batch_size=4,
        shuffle=True,
        num_workers=2,
    )

    model = Net()
    net = dist.dataparallel(model)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(net.parameters(), lr=0.001, momentum=0.9)

    for epoch in range(2):  # loop over the dataset multiple times

        running_loss = 0.0
        for i, data in enumerate(trainloader, 0):
            # get the inputs; data is a list of [inputs, labels]
            inputs, labels = data

            # zero the parameter gradients
            optimizer.zero_grad()

            # forward + backward + optimize
            outputs = net(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            # print statistics
            running_loss += loss.item()
            if i % 2000 == 1999:  # print every 2000 mini-batches
                print(f"[{epoch + 1}, {i + 1:5d}] loss: {running_loss / 2000:.3f}")
                running_loss = 0.0

    print("Finished Training")

    # Only one worker should save the network
    if dist.has_weight_autority():
        torch.save(model.state_dict(), PATH)


if __name__ == "__main__":
    # Usage:
    #
    #   # Works with a single GPU
    #   python distributed.py
    #
    #   # Works with multiple GPUs
    #   torchrun                            \
    #       --nproc_per_node=$GPU_COUNT     \
    #       --nnodes=$WORLD_SIZE            \
    #       --rdzv_id=$SLURM_JOB_ID         \
    #       --rdzv_backend=c10d             \
    #       --rdzv_endpoint=$RDV_ADDR       \
    #       distributed.py
    #
    import argparse

    parser = argparse.ArgumentParser()

    with dist.DistributedProcessGroup():
        train()
