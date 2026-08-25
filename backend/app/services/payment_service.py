"""Payment service with a real bug for meaningful Sentry test."""

class PaymentService:
    def charge(self, user, amount: int):
        # Bug: user may be None (guest checkout), but we access user.email without guard
        # This is the same pattern as the Ruby serializer but in Python
        receipt_email = user.email  # <-- crash site line 7
        if amount <= 0:
            raise ValueError("Amount must be positive")
        return {"email": receipt_email, "amount": amount, "status": "charged"}

    def refund(self, payment_id: str):
        if not payment_id:
            raise ValueError("payment_id required")
        return {"refunded": payment_id}
