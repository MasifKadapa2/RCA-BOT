"""Order service with a meaningful bug for RCA testing."""

class OrderService:
    def calculate_total(self, order):
        # Bug: order.items may be None for empty carts, but we iterate without check
        # This will raise TypeError: 'NoneType' object is not iterable
        total = 0
        for item in order.items:  # <-- crash site line 8
            total += item.price * item.quantity
        return total

    def apply_discount(self, total: float, discount_code: str):
        if discount_code == "SAVE20":
            return total * 0.8
        return total
