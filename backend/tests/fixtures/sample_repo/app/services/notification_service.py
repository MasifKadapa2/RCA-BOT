"""Notification service - unrelated to the serializer bug."""

import json


class NotificationService:
    def send(self, recipient, template, payload):
        body = json.dumps({"template": template, **payload})
        return f"sending to {recipient}: {body}"
