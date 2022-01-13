class Transformed:
    def __init__(self, dataset, transform=None):
        self.transform = transform
        self.dataset = dataset

    def __getitem__(self, idx):
        data, target = self.dataset[idx]

        if self.transform is not None:
            data = self.transform(data)

        return data, target

    def __len__(self):
        return len(self.dataset)
