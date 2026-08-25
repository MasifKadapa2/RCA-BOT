"""Allow `python -m app.slack` to start the Socket Mode listener."""

from app.bootstrap import build_container
from app.slack.handler import start_socket_mode

if __name__ == "__main__":
    start_socket_mode(build_container())
