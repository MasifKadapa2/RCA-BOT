"""Small auxiliary fixture repository used by deletion tests."""

import json


class EmailService:
    def send_welcome(self, address):
        payload = json.dumps({"to": address, "template": "welcome"})
        return f"queued {payload}"


def send_bulk(addresses):
    service = EmailService()
    return [service.send_welcome(a) for a in addresses]
