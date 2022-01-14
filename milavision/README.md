# milavision
Drop-in replacement for torchvision for working on Mila/ComputeCanada clusters


## Installation:
```pip install milavision```

## Usage:
Simply replace `torchvision.datasets` with `milavision.datasets`!
```python
from torchvision.datasets import CIFAR10, ImageNet
```
```python
from milavision.datasets import CIFAR10, ImageNet
```

