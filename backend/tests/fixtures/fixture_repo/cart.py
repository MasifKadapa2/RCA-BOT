def compute_total(items):
    return sum(item.price for item in items)


class Cart:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)
        return compute_total(self.items)
